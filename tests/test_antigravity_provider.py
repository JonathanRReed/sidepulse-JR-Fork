"""Antigravity's hooks.json integration, proved against a real shell.

Every claim here is anchored to Antigravity's own lifecycle-hooks
specification, which ships inside its language_server binary:

  * hooks.json is keyed by hook NAME; each named hook then holds event keys.
  * PreToolUse and PostToolUse take a grouped {matcher, hooks} shape; the
    other events take a flat handler list.
  * A command handler is {"type", "command", "timeout"}, run via `sh -c`.
  * The payload arrives on stdin as protojson and names the conversation
    (`conversationId`) but NEVER names the event that fired.
  * stdout is fed back to the agent; a non-zero exit is reported to the agent
    as a hook error.

The last three are why several tests below run the installed command through
a real /bin/sh instead of calling Python directly: the failure this provider
has to be protected from is not a wrong value, it is a command that is
registered, blocks the user's agent, and is never heard from.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sidepulse import install, providers
from sidepulse.capacity_types import SourceKey
from sidepulse.models import HookEvent, parse_datetime
from sidepulse.provider_adapters import (
    InertProviderRecord,
    NormalizedProviderRecord,
    ProviderEventName,
    minimize_hook_event,
    normalized_provider_record_to_payload,
    provider_facts_for_record,
)
from sidepulse.provider_contracts import CapabilityIdentifier
from sidepulse.provider_facts import ObservationAuthority, WorkLifecycle
from sidepulse.providers import negotiated_provider_sources

_CONVERSATION = "ec33ebf9-0cba-4100-8142-c61503f6c587"
_SOURCE = SourceKey("antigravity", "hooks", "global", "live_agent_events")


def _hook_source():
    return next(
        source
        for source in negotiated_provider_sources()
        if source.source_key == _SOURCE
    )


def _record(payload: dict[str, object], event: str = "Stop"):
    """Run one payload through the exact path a live hook takes."""
    line = json.dumps(
        {
            "hook_event_name": event,
            "antigravity": payload,
            "logged_at": "2026-08-14T12:00:00Z",
        }
    )
    parsed = providers.parse_log_line("antigravity", line)
    assert parsed is not None, "payload did not parse into a hook event"
    source = _hook_source()
    return minimize_hook_event(
        parsed,
        source_key=_SOURCE,
        contract=source.contract,
        observation_authority=source.registration.observation_authority,
    )


def _lifecycle(payload: dict[str, object], event: str = "Stop") -> WorkLifecycle:
    record = _record(payload, event)
    assert type(record) is NormalizedProviderRecord, record
    source = _hook_source()
    batch = provider_facts_for_record(
        record,
        contract=source.contract,
        observation_authority=source.registration.observation_authority,
        observed_at_epoch=1_800_000_000.0,
    )
    assert len(batch.work_facts) == 1, batch
    return batch.work_facts[0].lifecycle


def _install(tmp_path: Path, existing: dict | None = None) -> tuple[Path, Path]:
    config = providers.default_antigravity_config_path(tmp_path)
    config.parent.mkdir(parents=True, exist_ok=True)
    if existing is not None:
        config.write_text(json.dumps(existing, indent=2) + "\n")
    log = tmp_path / "antigravity.jsonl"
    install.install_antigravity_hooks(
        log_path=log,
        config_path=config,
        python_executable=sys.executable,
    )
    return config, log


def _run_installed_command(command: str, stdin: str) -> subprocess.CompletedProcess:
    """Execute exactly as Antigravity does: `sh -c <command>`, payload on stdin."""
    return subprocess.run(
        ["/bin/sh", "-c", command],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_antigravity_is_a_registered_hook_provider_with_its_native_event_keys() -> None:
    """The spec's .events are hooks.json CONFIG KEYS, not canonical events.

    Registering canonical names here would write a hooks.json whose keys
    Antigravity does not recognise: accepted config that never fires.
    """
    spec = providers.provider_spec("antigravity")

    assert spec.label == "Antigravity"
    assert spec.events == ("PreInvocation", "PostToolUse", "Stop")
    assert "antigravity" in providers.HOOK_PROVIDERS
    assert spec.config_path(Path("/home/x")) == Path("/home/x/.gemini/config/hooks.json")


def test_pretooluse_is_never_registered_for_antigravity() -> None:
    """Its stdout contract has no neutral value.

    `decision` is required and is one of allow/deny/ask/force_ask. Any value
    SidePulse emitted would override the user's own permission policy --
    "allow" would auto-approve every tool call in every session. A status bar
    does not get to decide what an agent may do.
    """
    assert "PreToolUse" not in providers.ANTIGRAVITY_EVENTS
    assert "PreToolUse" not in providers.ANTIGRAVITY_CANONICAL_EVENTS


def test_antigravity_native_event_names_stay_out_of_the_canonical_vocabulary() -> None:
    """PreInvocation is a config key; it must never be an ingested event name."""
    assert "PreInvocation" not in providers.KNOWN_EVENTS
    assert providers.canonical_event_name("PreInvocation") is None


def test_antigravity_hook_source_negotiates_and_is_invocable() -> None:
    """The gate that makes every other test here worth anything.

    A provider missing from the first-party contract table registers, installs,
    fires -- and is discarded at ingest, leaving the ledger reading "Idle"
    while the agent works. That is the exact outage this project opened on.
    """
    source = _hook_source()

    assert source.observation_invocation_allowed
    assert source.registration.observation_authority is (
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION
    )


def test_antigravity_declares_no_actionable_requests_capability() -> None:
    """PreToolUse is the only Antigravity event that names a user decision.

    Since it is never installed, nothing in this feed can carry a live request,
    and declaring the capability would leave a supported-looking request lane
    that is permanently empty.
    """
    declared = {
        source.declared_capability_id
        for source in negotiated_provider_sources()
        if source.source_key.provider_id == "antigravity"
    }

    assert declared == {CapabilityIdentifier("live_agent_events")}


# --------------------------------------------------------------------------
# Installation
# --------------------------------------------------------------------------


def test_install_uses_the_grouped_shape_only_for_tool_events(tmp_path: Path) -> None:
    """Antigravity requires {matcher, hooks} for PostToolUse and a flat list
    for PreInvocation and Stop. The wrong shape is silently inert config."""
    config, _log = _install(tmp_path)
    entry = json.loads(config.read_text())["sidepulse-status"]

    assert entry["enabled"] is True
    assert list(entry["PostToolUse"][0]) == ["matcher", "hooks"]
    assert entry["PostToolUse"][0]["matcher"] == "*"
    assert entry["PostToolUse"][0]["hooks"][0]["type"] == "command"
    for flat_event in ("PreInvocation", "Stop"):
        assert "hooks" not in entry[flat_event][0], flat_event
        assert entry[flat_event][0]["type"] == "command"
        assert entry[flat_event][0]["timeout"] == install.ANTIGRAVITY_HOOK_TIMEOUT_SECONDS


def test_install_preserves_other_tools_named_hooks(tmp_path: Path) -> None:
    """hooks.json is a shared user-level file keyed by hook name."""
    foreign = {
        "team-linter": {
            "PostToolUse": [
                {"matcher": "run_command", "hooks": [{"command": "./lint.sh"}]}
            ]
        }
    }
    config, _log = _install(tmp_path, existing=dict(foreign))
    data = json.loads(config.read_text())

    assert data["team-linter"] == foreign["team-linter"]
    assert "sidepulse-status" in data


def test_reinstall_changes_nothing_and_uninstall_removes_only_our_entry(
    tmp_path: Path,
) -> None:
    foreign = {"team-linter": {"Stop": [{"command": "./ship.sh"}]}}
    config, log = _install(tmp_path, existing=dict(foreign))

    again = install.install_antigravity_hooks(
        log_path=log, config_path=config, python_executable=sys.executable
    )
    assert again.changed is False

    install.uninstall_antigravity_hooks(log_path=log, config_path=config)
    data = json.loads(config.read_text())

    assert "sidepulse-status" not in data
    assert data["team-linter"] == foreign["team-linter"]


def test_install_refuses_to_overwrite_an_unowned_hook_of_the_same_name(
    tmp_path: Path,
) -> None:
    config = providers.default_antigravity_config_path(tmp_path)
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"sidepulse-status": {"Stop": [{"command": "./not-ours.sh"}]}}) + "\n"
    )

    with pytest.raises(OSError):
        install.install_antigravity_hooks(
            log_path=tmp_path / "a.jsonl",
            config_path=config,
            python_executable=sys.executable,
        )
    assert "not-ours.sh" in config.read_text()


def test_detection_reports_installed_only_for_our_own_enabled_commands(
    tmp_path: Path,
) -> None:
    config, log = _install(tmp_path)
    detected = providers.detect_antigravity_config(tmp_path)
    assert detected.hooks_enabled is True
    assert detected.hook_events == ("PostToolUse", "PreInvocation", "Stop")
    assert detected.log_paths == (log,)

    data = json.loads(config.read_text())
    data["sidepulse-status"]["enabled"] = False
    config.write_text(json.dumps(data))
    disabled = providers.detect_antigravity_config(tmp_path)

    assert disabled.hooks_enabled is False
    assert disabled.hook_events == ()


def test_detection_ignores_a_foreign_command_under_our_hook_name(
    tmp_path: Path,
) -> None:
    """Counting someone else's command as ours reports a working install
    for hooks that will never reach SidePulse."""
    config = providers.default_antigravity_config_path(tmp_path)
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {"sidepulse-status": {"Stop": [{"command": "echo hi --log /tmp/x.jsonl"}]}}
        )
    )

    assert providers.detect_antigravity_config(tmp_path).hooks_enabled is False


# --------------------------------------------------------------------------
# The installed command, executed by a real shell
# --------------------------------------------------------------------------


def test_installed_command_writes_a_record_when_run_by_a_real_shell(
    tmp_path: Path,
) -> None:
    """The whole point: registered, executed by sh, and actually heard."""
    config, log = _install(tmp_path)
    command = json.loads(config.read_text())["sidepulse-status"]["Stop"][0]["command"]

    result = _run_installed_command(
        command,
        json.dumps(
            {
                "conversationId": _CONVERSATION,
                "executionNum": 1,
                "terminationReason": "model_stop",
                "fullyIdle": True,
            }
        ),
    )

    assert result.returncode == 0
    written = [line for line in log.read_text().splitlines() if line.strip()]
    assert len(written) == 1
    record = json.loads(written[0])
    assert record["provider_id"] == "antigravity"
    assert record["event_name"] == "stop"
    assert record["provider_work_id"] == _CONVERSATION


def test_installed_command_stamps_the_event_name_the_registration_site_knows(
    tmp_path: Path,
) -> None:
    """Antigravity's payload never says which hook fired.

    Without the stamp every event would arrive nameless and be dropped, so
    the provider would install cleanly and report nothing forever.
    """
    config, log = _install(tmp_path)
    entry = json.loads(config.read_text())["sidepulse-status"]
    payload = json.dumps({"conversationId": _CONVERSATION, "invocationNum": 1})

    _run_installed_command(entry["PreInvocation"][0]["command"], payload)
    _run_installed_command(entry["PostToolUse"][0]["hooks"][0]["command"], payload)

    names = [json.loads(line)["event_name"] for line in log.read_text().splitlines() if line.strip()]
    assert names == ["user_prompt_submit", "post_tool_use"]


def test_installed_command_returns_the_documented_no_op_and_never_fails(
    tmp_path: Path,
) -> None:
    """Antigravity feeds a hook's stdout back to the agent and reports a
    non-zero exit to it as an error. SidePulse must be silent and harmless
    even when it is completely broken -- here, pointed at an unwritable log."""
    config, _log = _install(tmp_path)
    command = json.loads(config.read_text())["sidepulse-status"]["Stop"][0]["command"]
    broken = command.replace(str(_log_path_in(command)), "/proc/nonexistent/x.jsonl")

    healthy = _run_installed_command(command, json.dumps({"conversationId": _CONVERSATION}))
    assert healthy.returncode == 0
    assert healthy.stdout == "{}"

    failing = _run_installed_command(broken, json.dumps({"conversationId": _CONVERSATION}))
    assert failing.returncode == 0
    assert failing.stdout == "{}"


def _log_path_in(command: str) -> Path:
    return providers.extract_log_paths_from_command(command)[0]


def test_installed_command_survives_an_empty_payload(tmp_path: Path) -> None:
    """A malformed envelope would be dropped at parse; the shell must still
    exit 0 with {} so a surprise from Antigravity cannot surface in the
    user's session."""
    config, _log = _install(tmp_path)
    command = json.loads(config.read_text())["sidepulse-status"]["Stop"][0]["command"]

    result = _run_installed_command(command, "")

    assert result.returncode == 0
    assert result.stdout == "{}"


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def test_conversation_id_becomes_the_work_identity() -> None:
    record = _record({"conversationId": _CONVERSATION, "terminationReason": "model_stop"})

    assert type(record) is NormalizedProviderRecord
    assert record.provider_work_id is not None
    assert record.provider_work_id.value == _CONVERSATION
    assert record.safe_label == f"Antigravity {_CONVERSATION}"


def test_workspace_and_transcript_paths_never_reach_the_record() -> None:
    """The payload carries the user's file paths. The ledger has no use for
    them, so nothing but the conversation id is lifted out of the envelope."""
    record = _record(
        {
            "conversationId": _CONVERSATION,
            "workspacePaths": ["/Users/private/secret-client"],
            "transcriptPath": "/Users/private/secret-client/.gemini/transcript.jsonl",
            "artifactDirectoryPath": "/Users/private/secret-client/.gemini/artifacts",
            "terminationReason": "model_stop",
        }
    )
    encoded = json.dumps(normalized_provider_record_to_payload(record))

    assert "secret-client" not in encoded


@pytest.mark.parametrize(
    "conversation_id",
    [
        "x" * 200,
        "conv id with spaces",
        "api_key.abcdef",
        "",
        {"unexpected": "shape"},
    ],
)
def test_a_conversation_id_that_is_not_an_opaque_token_fails_closed(
    conversation_id: object,
) -> None:
    """conversationId is the only identity this provider has, and it becomes a
    row label. An over-long, malformed or credential-shaped value is refused
    rather than displayed."""
    record = _record({"conversationId": conversation_id, "terminationReason": "model_stop"})

    assert type(record) is InertProviderRecord
    assert record.diagnostic.identifier.value == "invalid_provider_identity"


def test_an_event_with_no_conversation_id_is_reported_as_missing_identity() -> None:
    """Antigravity can hand a hook a payload we cannot attribute. That has to
    read as a known gap, not as a silently invented unit of work."""
    record = _record({"terminationReason": "model_stop"})
    assert type(record) is NormalizedProviderRecord
    assert record.provider_work_id is None

    source = _hook_source()
    batch = provider_facts_for_record(
        record,
        contract=source.contract,
        observation_authority=source.registration.observation_authority,
        observed_at_epoch=1_800_000_000.0,
    )

    assert batch.work_facts == ()
    assert [d.identifier.value for d in batch.diagnostics] == ["missing_work_identity"]


# --------------------------------------------------------------------------
# Outcome refinement
# --------------------------------------------------------------------------


def test_stop_reasons_map_to_their_real_outcomes() -> None:
    base = {"conversationId": _CONVERSATION, "fullyIdle": True}

    assert _lifecycle({**base, "terminationReason": "model_stop"}) is WorkLifecycle.COMPLETED
    assert (
        _lifecycle({**base, "terminationReason": "error", "error": "boom"})
        is WorkLifecycle.FAILED
    )
    assert (
        _lifecycle({**base, "terminationReason": "max_steps_exceeded"})
        is WorkLifecycle.UNKNOWN
    )


def test_an_unrecognised_termination_reason_still_ends_the_work() -> None:
    """Antigravity documents the reason list with an "e.g." -- the set is
    open. Dropping an unlisted reason would strand the work ACTIVE in the
    ledger forever, which is worse than saying "ended, outcome unknown"."""
    record = _record(
        {
            "conversationId": _CONVERSATION,
            "terminationReason": "some_reason_shipped_after_this_release",
            "fullyIdle": True,
        }
    )

    assert type(record) is NormalizedProviderRecord
    assert record.event_name is ProviderEventName.STOP_INCOMPLETE


def test_stop_with_background_work_outstanding_is_not_completed() -> None:
    """fullyIdle is Antigravity telling us the parent is not finished.

    Claiming COMPLETED here is the failure this project already paid for
    once: a parent reported done while work it started was still running.
    """
    assert (
        _lifecycle(
            {
                "conversationId": _CONVERSATION,
                "terminationReason": "model_stop",
                "fullyIdle": False,
            }
        )
        is not WorkLifecycle.COMPLETED
    )


def test_a_failing_tool_call_does_not_mark_the_work_failed() -> None:
    """PostToolUse carries the tool's own exit status and the loop keeps
    running. An agent whose test command exits 1 has not failed, and the
    blocked light blinks until it is dealt with."""
    lifecycle = _lifecycle(
        {"conversationId": _CONVERSATION, "stepIdx": 5, "error": "exit status 1"},
        event="PostToolUse",
    )

    assert lifecycle is WorkLifecycle.ACTIVE


def test_a_non_string_outcome_field_fails_closed() -> None:
    record = _record(
        {"conversationId": _CONVERSATION, "terminationReason": {"unexpected": "shape"}}
    )

    assert type(record) is InertProviderRecord
    assert record.diagnostic.identifier.value == "invalid_provider_outcome"


def test_unmapped_antigravity_event_names_are_dropped_not_guessed() -> None:
    """A hand-written hook that forwarded a raw native name must not be
    silently reinterpreted as some neighbouring event."""
    line = json.dumps(
        {
            "hook_event_name": "PostInvocation",
            "antigravity": {"conversationId": _CONVERSATION},
            "logged_at": "2026-08-14T12:00:00Z",
        }
    )

    assert providers.parse_log_line("antigravity", line) is None


def test_hook_command_rejects_an_event_name_the_envelope_cannot_emit() -> None:
    with pytest.raises(ValueError):
        install.antigravity_hook_command("PreToolUse", Path("/tmp/x.jsonl"))


def test_unparsed_payloads_never_become_a_hook_event() -> None:
    """Guards the envelope: a bare Antigravity payload has no event name."""
    bare = json.dumps({"conversationId": _CONVERSATION, "terminationReason": "model_stop"})

    assert providers.parse_log_line("antigravity", bare) is None


def test_hook_event_round_trip_keeps_the_declared_provider() -> None:
    parsed = providers.parse_log_line(
        "antigravity",
        json.dumps(
            {
                "hook_event_name": "Stop",
                "antigravity": {"conversationId": _CONVERSATION},
                "logged_at": "2026-08-14T12:00:00Z",
            }
        ),
    )

    assert type(parsed) is HookEvent
    assert parsed.provider == "antigravity"
    assert parsed.logged_at == parse_datetime("2026-08-14T12:00:00Z")
