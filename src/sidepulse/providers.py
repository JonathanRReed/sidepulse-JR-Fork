from __future__ import annotations

import json
import os
import re
import shlex
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capacity_types import SourceKey
from .models import HookEvent, parse_datetime
from .origin import origin_label_from_payload
from .private_io import read_private_text
from .provider_contracts import (
    AdapterIdentifier,
    CapabilityAuthority,
    CapabilityIdentifier,
    ContractValidationError,
    NegotiatedCapability,
    NegotiatedProviderContract,
    ProviderIdentifier,
    SchemaVersion,
    SourceInstanceIdentifier,
    negotiate_provider_contract,
    provider_contract_document,
)
from .provider_facts import ObservationAuthority

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    tomllib = None

CODEX_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "PreCompact",
    "PostCompact",
    "SubagentStart",
    "SubagentStop",
    "Stop",
)

CLAUDE_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "Notification",
    "PreCompact",
    "PostCompact",
    "SubagentStop",
    "Stop",
    "SessionEnd",
)

GROK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionDenied",
    "Notification",
    "PreCompact",
    "PostCompact",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "StopFailure",
    "SessionEnd",
)

DEVIN_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "PostCompaction",
    "Stop",
    "SessionEnd",
)

# Cursor's own native hook event names (camelCase, per ~/.cursor/hooks.json's
# documented schema) -- registered verbatim, canonicalized on ingest by
# canonical_event_name's alias table. The cursor-agent CLI currently only
# emits the shell-execution pair; the IDE emits the full set.
CURSOR_EVENTS = (
    "sessionStart",
    "beforeSubmitPrompt",
    "preToolUse",
    "postToolUse",
    "postToolUseFailure",
    "beforeShellExecution",
    "afterShellExecution",
    "beforeMCPExecution",
    "afterMCPExecution",
    "subagentStart",
    "subagentStop",
    "preCompact",
    "stop",
    "sessionEnd",
)

# Hermes Agent's native plugin-hook event names (snake_case, declared under
# the hooks: block of ~/.hermes/config.yaml). Turn outcome, session teardown,
# and provider-attempt errors remain distinct at the adapter boundary.
HERMES_EVENTS = (
    "on_session_start",
    "pre_llm_call",
    "pre_tool_call",
    "post_tool_call",
    "subagent_start",
    "subagent_stop",
    "on_session_end",
    "on_session_finalize",
    "api_request_error",
)

# OpenClaw hooks are in-gateway JS handlers, not shell commands -- the
# installed handler (see install.openclaw_handler_source) translates the
# gateway's own events to these canonical names before forwarding, so
# the Python side only ever sees canonical events.
OPENCLAW_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "SessionEnd",
)

# The OpenCode global plugin bridge emits only canonical SidePulse event names.
# Native OpenCode events remain inside the installed bridge.
OPENCODE_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "Notification",
    "PreCompact",
    "PostCompact",
    "Stop",
    "StopFailure",
    "SessionEnd",
)

# Antigravity's OWN hooks.json event keys -- the three we register, verbatim
# from the lifecycle-hooks specification shipped inside its language_server
# binary (the same binary that parses hooks.json). Its full documented set is
# PreToolUse, PostToolUse, PreInvocation, PostInvocation and Stop; the two we
# do not register are refused deliberately, not for want of evidence:
#
#   PreToolUse   -- its stdout contract makes `decision` REQUIRED, one of
#                   allow/deny/ask/force_ask, with no documented "no opinion"
#                   value. Every value SidePulse could emit would override the
#                   user's own permission policy ("allow" auto-approves every
#                   tool call). A status bar must never decide what an agent is
#                   allowed to do, so this event stays uninstalled.
#   PostInvocation -- yields the identical lifecycle fact as PreInvocation (the
#                   work is still ACTIVE) while adding a second hook to a loop
#                   Antigravity documents as synchronous and blocking. Paying
#                   the user's agent latency twice for one fact is not a trade
#                   the ledger needs.
ANTIGRAVITY_EVENTS = (
    "PreInvocation",
    "PostToolUse",
    "Stop",
)

# Only PreToolUse/PostToolUse take Antigravity's grouped {matcher, hooks} shape;
# the rest are flat handler lists. Registering the wrong shape is silently
# accepted config that never fires.
ANTIGRAVITY_GROUPED_EVENTS = frozenset({"PreToolUse", "PostToolUse"})

# Antigravity's hook payload carries NO event name -- its stdin object holds
# conversationId, workspacePaths, transcriptPath, artifactDirectoryPath,
# modelName and the per-event args, and nothing that says which hook fired. The
# installed hook command therefore wraps each payload in a canonical envelope
# before forwarding, exactly as the OpenClaw handler and the OpenCode plugin
# translate their gateways' native events. These are the only names that
# envelope ever emits; Stop and PostToolUse are refined into their failure
# variants at the adapter boundary, from terminationReason and error.
ANTIGRAVITY_CANONICAL_EVENTS = {
    "PreInvocation": "UserPromptSubmit",
    "PostToolUse": "PostToolUse",
    "Stop": "Stop",
}

# Kiro CLI scopes hooks to agent configuration files; SidePulse owns one
# dedicated agent file (~/.kiro/agents/sidepulse.json) and never edits an
# unmanaged one. Kiro's native event names are camelCase (agentSpawn,
# userPromptSubmit, preToolUse, postToolUse, stop) and normalize through
# canonical_event_name; only agentSpawn needs an explicit alias.
KIRO_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
)
KIRO_NATIVE_EVENT_NAMES = {
    "SessionStart": "agentSpawn",
    "UserPromptSubmit": "userPromptSubmit",
    "PreToolUse": "preToolUse",
    "PostToolUse": "postToolUse",
    "Stop": "stop",
}
KIRO_MANAGED_DESCRIPTION = (
    "Kiro agent with SidePulse lifecycle monitoring enabled."
)

ANTIGRAVITY_HOOK_NAME = "sidepulse-status"
ANTIGRAVITY_ENVELOPE_KEY = "antigravity"

OPENCODE_PLUGIN_MARKER = "sidepulse-opencode-plugin-v1"
_OPENCODE_PLUGIN_MAX_SOURCE_BYTES = 32 * 1024


def _is_sidepulse_hook_invocation(parts) -> bool:
    """Ours, in any of the three shapes we have ever registered.

    Legacy installs name hook_entry.py directly; today's invoke the
    module so the entry resolves wherever the package lives; frozen
    builds call the bundled agent-monitor subcommand. A recognizer that
    knew only the first shape would report "hooks not installed" for a
    perfectly working install and re-register duplicates over it.
    """
    parts = list(parts)
    if any(Path(part).name == "hook_entry.py" for part in parts):
        return True
    if "sidepulse.hook_entry" in parts and "-m" in parts:
        return True
    return "agent-monitor" in parts and "hook-log" in parts


def _valid_opencode_hook_arguments(
    arguments: object,
) -> tuple[str, ...] | None:
    if not isinstance(arguments, list) or len(arguments) not in (6, 7):
        return None
    if not all(
        isinstance(argument, str)
        and argument
        and argument.isascii()
        and not any(ord(char) < 32 for char in argument)
        for argument in arguments
    ):
        return None
    package_hook_entry = str(Path(__file__).with_name("hook_entry.py").resolve())
    executable_trusted = False
    if Path(arguments[0]).is_absolute():
        try:
            executable_trusted = Path(arguments[0]).resolve() == Path(sys.executable).resolve()
        except OSError:
            executable_trusted = False
    python_shape = (
        len(arguments) == 6
        and Path(arguments[0]).is_absolute()
        and executable_trusted
        and arguments[1] == package_hook_entry
        and arguments[2:5] == ["--provider", "opencode", "--log"]
    )
    # Today's registrations invoke the module, so the entry resolves
    # wherever the package actually lives. Both forms are ours.
    module_shape = (
        len(arguments) == 7
        and Path(arguments[0]).is_absolute()
        and executable_trusted
        and arguments[1] == "-m"
        and arguments[2] == "sidepulse.hook_entry"
        and arguments[3:6] == ["--provider", "opencode", "--log"]
    )
    frozen_shape = (
        len(arguments) == 7
        and Path(arguments[0]).is_absolute()
        and executable_trusted
        and arguments[1:3] == ["agent-monitor", "hook-log"]
        and arguments[3:6] == ["--provider", "opencode", "--log"]
    )
    if not (python_shape or module_shape or frozen_shape):
        return None
    if len(arguments[-1]) > 4096 or "\x00" in arguments[-1]:
        return None
    return tuple(arguments)


def opencode_plugin_source_for_arguments(hook_arguments: list[str] | tuple[str, ...]) -> str:
    """Build the dependency-free, content-free OpenCode event bridge."""
    arguments = _valid_opencode_hook_arguments(list(hook_arguments))
    if arguments is None:
        raise ValueError("invalid OpenCode hook arguments")
    encoded_arguments = json.dumps(arguments, separators=(",", ":"))
    return f'''// {OPENCODE_PLUGIN_MARKER}
const SIDEPULSE_HOOK_ARGS = Object.freeze({encoded_arguments});
const SIDEPULSE_MAX_ID_LENGTH = 128;
const SIDEPULSE_MAX_PAYLOAD_BYTES = 1024;

function opaqueIdentifier(value) {{
  return typeof value === "string"
    && value.length > 0
    && value.length <= SIDEPULSE_MAX_ID_LENGTH
    && /^[A-Za-z0-9._:-]+$/.test(value)
    && !/^(?:sk|token|secret|api[_-]?key)[._:-]/i.test(value)
    ? value : undefined;
}}

function boundedSequence(value) {{
  return Number.isSafeInteger(value) && value >= 0 && value <= 1000000000
    ? value
    : undefined;
}}

function boundedTimestamp(value) {{
  return typeof value === "string"
    && value.length <= 64
    && /^\\d{{4}}-\\d{{2}}-\\d{{2}}T\\d{{2}}:\\d{{2}}:\\d{{2}}(?:\\.\\d{{1,9}})?Z$/.test(value)
    ? value
    : undefined;
}}

function eventName(event) {{
  return event && typeof event.type === "string" ? event.type : undefined;
}}

function canonicalEvent(event) {{
  const name = eventName(event);
  if (name === "session.status") {{
    return event.properties?.status?.type === "active" || event.properties?.status?.type === "busy" ? "UserPromptSubmit" : undefined;
  }}
  return {{
    "session.created": "SessionStart",
    "session.idle": "Stop",
    "session.error": "StopFailure",
    "permission.asked": "PermissionRequest",
    "permission.replied": "PostToolUse",
    "question.asked": "Notification",
    "question.replied": "PostToolUse",
    "question.rejected": "PostToolUse",
    "tool.execute.before": "PreToolUse",
    "tool.execute.after": "PostToolUse",
    "session.compacting": "PreCompact",
    "session.compact.before": "PreCompact",
    "session.compacted": "PostCompact",
    "session.compact.after": "PostCompact",
  }}[name];
}}

function payloadFor(event) {{
  if (!event || typeof event !== "object") return undefined;
  const hookEventName = canonicalEvent(event);
  if (!hookEventName) return undefined;
  const payload = {{ hook_event_name: hookEventName }};
  const properties = event.properties;
  if (!properties || typeof properties !== "object" || Array.isArray(properties)) return undefined;
  const sessionId = opaqueIdentifier(properties.sessionID ?? properties.sessionId);
  const workId = opaqueIdentifier(properties.workID ?? properties.workId);
  const requestId = opaqueIdentifier(properties.requestID ?? properties.requestId);
  if ((properties.sessionID ?? properties.sessionId) !== undefined && !sessionId) return undefined;
  if ((properties.workID ?? properties.workId) !== undefined && !workId) return undefined;
  if ((properties.requestID ?? properties.requestId) !== undefined && !requestId) return undefined;
  const sequence = boundedSequence(properties.sequence);
  const timestamp = boundedTimestamp(properties.timestamp);
  if (sessionId) payload.session_id = sessionId;
  if (workId) payload.work_id = workId;
  if (requestId) payload.request_id = requestId;
  if (sequence !== undefined) payload.sequence = sequence;
  if (timestamp) payload.timestamp = timestamp;
  if (hookEventName === "Notification") payload.notification_kind = "input_required";
  const encoded = JSON.stringify(payload);
  return encoded.length <= SIDEPULSE_MAX_PAYLOAD_BYTES ? encoded : undefined;
}}

function forward(encodedPayload) {{
  try {{
    const child = Bun.spawn(SIDEPULSE_HOOK_ARGS, {{ stdin: "pipe", stdout: "ignore", stderr: "ignore" }});
    child.stdin.write(encodedPayload);
    child.stdin.end();
    child.unref?.();
  }} catch {{}}
}}

const SidePulsePlugin = {{
  event: async ({{ event }}) => {{
    const payload = payloadFor(event);
    if (payload) forward(payload);
  }},
}};

export default SidePulsePlugin;
'''


def managed_opencode_plugin_log_path(text: str) -> Path | None:
    marker = f"// {OPENCODE_PLUGIN_MARKER}\nconst SIDEPULSE_HOOK_ARGS = Object.freeze("
    if not text.startswith(marker):
        return None
    end = text.find(");\n", len(marker))
    if end < 0:
        return None
    try:
        arguments = json.loads(text[len(marker):end])
    except json.JSONDecodeError:
        return None
    valid_arguments = _valid_opencode_hook_arguments(arguments)
    if valid_arguments is None:
        return None
    if text != opencode_plugin_source_for_arguments(valid_arguments):
        return None
    return Path(valid_arguments[-1])


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    config_path: Path
    exists: bool
    hooks_enabled: bool
    hook_events: tuple[str, ...]
    log_paths: tuple[Path, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "config_path": str(self.config_path),
            "exists": self.exists,
            "hooks_enabled": self.hooks_enabled,
            "hook_events": list(self.hook_events),
            "log_paths": [str(path) for path in self.log_paths],
        }


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    label: str
    events: tuple[str, ...]
    config_kind: str
    config_path: Callable[[Path | None], Path]
    detector: Callable[[Path | None], ProviderConfig]


@dataclass(frozen=True, slots=True)
class ProviderSourceRegistration:
    provider_id: ProviderIdentifier
    adapter_id: AdapterIdentifier
    source_instance_id: SourceInstanceIdentifier
    observation_authority: ObservationAuthority
    capability_versions: tuple[
        tuple[CapabilityIdentifier, tuple[SchemaVersion, ...]], ...
    ]

    def __post_init__(self) -> None:
        if not (
            type(self.provider_id) is ProviderIdentifier
            and type(self.adapter_id) is AdapterIdentifier
            and type(self.source_instance_id) is SourceInstanceIdentifier
            and type(self.observation_authority) is ObservationAuthority
            and type(self.capability_versions) is tuple
            and self.capability_versions
        ):
            raise ContractValidationError("invalid provider source registration")
        for row in self.capability_versions:
            if not (
                type(row) is tuple
                and len(row) == 2
                and type(row[0]) is CapabilityIdentifier
                and type(row[1]) is tuple
                and row[1]
                and all(type(version) is SchemaVersion for version in row[1])
            ):
                raise ContractValidationError("invalid provider source registration")
        capability_ids = tuple(row[0] for row in self.capability_versions)
        if len(capability_ids) != len(set(capability_ids)):
            raise ContractValidationError("duplicate registered capability")


@dataclass(frozen=True, slots=True)
class NegotiatedProviderSource:
    source_key: SourceKey
    registration: ProviderSourceRegistration
    contract: NegotiatedProviderContract
    declared_capability_id: CapabilityIdentifier
    declared_capability_version: SchemaVersion
    negotiated_capability: NegotiatedCapability | None

    def __post_init__(self) -> None:
        if not (
            type(self.source_key) is SourceKey
            and type(self.registration) is ProviderSourceRegistration
            and type(self.contract) is NegotiatedProviderContract
            and type(self.declared_capability_id) is CapabilityIdentifier
            and type(self.declared_capability_version) is SchemaVersion
            and (
                self.negotiated_capability is None
                or type(self.negotiated_capability) is NegotiatedCapability
            )
        ):
            raise ContractValidationError("invalid negotiated provider source")
        expected_key = SourceKey(
            self.registration.provider_id.value,
            self.registration.adapter_id.value,
            self.registration.source_instance_id.value,
            self.declared_capability_id.value,
        )
        if self.source_key != expected_key:
            raise ContractValidationError("invalid negotiated provider source")

    @property
    def observation_invocation_allowed(self) -> bool:
        """Whether this exact capability row is eligible for read invocation."""
        return (
            self.negotiated_capability is not None
            and self.negotiated_capability.authority
            in {CapabilityAuthority.DISCOVERY, CapabilityAuthority.OBSERVATION}
            and self.contract.observation_invocation_allowed
        )


def default_state_dir(home: Path | None = None) -> Path:
    if home is None:
        xdg_state_home = os.environ.get("XDG_STATE_HOME")
        if xdg_state_home:
            return Path(xdg_state_home).expanduser() / "sidepulse" / "agent-monitor"

    base = home or Path.home()
    return base / ".local" / "state" / "sidepulse" / "agent-monitor"


def default_log_path(provider: str, home: Path | None = None) -> Path:
    suffix = "jsonl"
    return default_state_dir(home) / f"{provider}.{suffix}"


def default_codex_config_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / ".codex" / "config.toml"


def detect_codex_config(home: Path | None = None) -> ProviderConfig:
    config_path = default_codex_config_path(home)
    if not config_path.exists():
        return ProviderConfig("codex", config_path, False, False, (), ())

    text = config_path.read_text()
    try:
        if tomllib is None:
            raise RuntimeError("tomllib unavailable")
        data = tomllib.loads(text)
    except Exception:
        return detect_codex_config_from_text(config_path, text)

    features = data.get("features") or {}
    hooks = data.get("hooks") or {}
    hook_events: list[str] = []
    paths: list[Path] = []

    if isinstance(hooks, dict):
        for event_name, entries in hooks.items():
            if event_name not in CODEX_EVENTS or not isinstance(entries, list):
                continue
            hook_events.append(event_name)
            paths.extend(_paths_from_hook_entries(entries))

    return ProviderConfig(
        "codex",
        config_path,
        True,
        bool(features.get("hooks")),
        tuple(sorted(set(hook_events))),
        _dedupe_paths(paths),
    )


def detect_codex_config_from_text(config_path: Path, text: str) -> ProviderConfig:
    hook_events = tuple(
        sorted(
            {
                match.group(1)
                for match in re.finditer(r"^\s*\[\[hooks\.([A-Za-z0-9_]+)\]\]\s*$", text, re.MULTILINE)
                if match.group(1) in CODEX_EVENTS
            }
        )
    )

    paths: list[Path] = []
    for match in re.finditer(r"command\s*=\s*'''(.*?)'''", text, re.DOTALL):
        paths.extend(extract_log_paths_from_command(match.group(1)))
    for match in re.finditer(r'command\s*=\s*"(.*?)"', text):
        paths.extend(extract_log_paths_from_command(match.group(1)))

    return ProviderConfig(
        "codex",
        config_path,
        True,
        codex_hooks_feature_enabled(text),
        hook_events,
        _dedupe_paths(paths),
    )


def codex_hooks_feature_enabled(text: str) -> bool:
    match = re.search(r"^\s*\[features\]\s*$(.*?)(?=^\s*\[|\Z)", text, re.MULTILINE | re.DOTALL)
    if not match:
        return False
    return bool(re.search(r"^\s*hooks\s*=\s*true\s*$", match.group(1), re.MULTILINE))


def default_claude_config_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / ".claude" / "settings.json"


def detect_json_hook_config(
    provider: str,
    config_path: Path,
    allowed_events: tuple[str, ...],
    command_filter: Callable[[str], bool] | None = None,
) -> ProviderConfig:
    if not config_path.exists():
        return ProviderConfig(provider, config_path, False, False, (), ())

    try:
        data = json.loads(config_path.read_text())
    except Exception:
        return ProviderConfig(provider, config_path, True, False, (), ())

    hooks = data.get("hooks") or {}
    hook_events: list[str] = []
    paths: list[Path] = []

    if isinstance(hooks, dict):
        for event_name, entries in hooks.items():
            canonical = canonical_event_name(event_name)
            if (
                event_name not in allowed_events
                and canonical not in allowed_events
            ) or not isinstance(entries, list):
                continue
            event_paths = _paths_from_hook_entries(entries, command_filter)
            if command_filter is not None and not event_paths:
                continue
            hook_events.append(canonical)
            paths.extend(event_paths)

    return ProviderConfig(
        provider,
        config_path,
        True,
        bool(hook_events),
        tuple(sorted(set(hook_events))),
        _dedupe_paths(paths),
    )


def detect_claude_config(home: Path | None = None) -> ProviderConfig:
    return detect_json_hook_config(
        "claude", default_claude_config_path(home), CLAUDE_EVENTS
    )


def default_devin_config_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / ".config" / "devin" / "config.json"


def detect_devin_config(home: Path | None = None) -> ProviderConfig:
    return detect_json_hook_config(
        "devin",
        default_devin_config_path(home),
        DEVIN_EVENTS,
        is_sidepulse_devin_command,
    )


def default_grok_hook_config_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / ".grok" / "hooks" / "sidepulse.json"


def detect_grok_config(home: Path | None = None) -> ProviderConfig:
    config_path = default_grok_hook_config_path(home)
    return detect_json_hook_config("grok", config_path, GROK_EVENTS)


def is_sidepulse_hook_command(command: str, provider: str) -> bool:
    """True when `command` is one of SidePulse's own hook commands for
    `provider` -- the generic form of is_sidepulse_devin_command, used by
    installers/uninstallers and detectors that must never count or touch
    another tool's hooks in a shared config file."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    has_provider = any(
        (part == "--provider" and index + 1 < len(parts) and parts[index + 1] == provider)
        or part == f"--provider={provider}"
        for index, part in enumerate(parts)
    )
    if not has_provider:
        return False
    return _is_sidepulse_hook_invocation(parts)


def default_cursor_config_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / ".cursor" / "hooks.json"


def detect_cursor_config(home: Path | None = None) -> ProviderConfig:
    """Cursor's hooks.json holds FLAT entries ({"command": ...} directly,
    not Claude's nested {"hooks": [...]} shape) and is a shared user-level
    file other tools also write to -- only SidePulse's own commands count
    toward installed-ness here."""
    config_path = default_cursor_config_path(home)
    if not config_path.exists():
        return ProviderConfig("cursor", config_path, False, False, (), ())
    try:
        data = json.loads(config_path.read_text())
    except Exception:
        return ProviderConfig("cursor", config_path, True, False, (), ())

    hooks = data.get("hooks") or {}
    hook_events: list[str] = []
    paths: list[Path] = []
    if isinstance(hooks, dict):
        for event_name, entries in hooks.items():
            if event_name not in CURSOR_EVENTS or not isinstance(entries, list):
                continue
            event_paths: list[Path] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                command = entry.get("command")
                if isinstance(command, str) and is_sidepulse_hook_command(command, "cursor"):
                    event_paths.extend(extract_log_paths_from_command(command))
            if event_paths:
                hook_events.append(event_name)
                paths.extend(event_paths)

    return ProviderConfig(
        "cursor",
        config_path,
        True,
        bool(hook_events),
        tuple(sorted(set(hook_events))),
        _dedupe_paths(paths),
    )


def default_hermes_config_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / ".hermes" / "config.yaml"


def detect_hermes_config(home: Path | None = None) -> ProviderConfig:
    config_path = default_hermes_config_path(home)
    if not config_path.exists():
        return ProviderConfig("hermes", config_path, False, False, (), ())
    try:
        from ruamel.yaml import YAML

        data = YAML(typ="safe").load(config_path.read_text()) or {}
    except Exception:
        return ProviderConfig("hermes", config_path, True, False, (), ())

    hooks = data.get("hooks") if isinstance(data, dict) else {}
    hook_events: list[str] = []
    paths: list[Path] = []
    if isinstance(hooks, dict):
        for event_name, entries in hooks.items():
            if event_name not in HERMES_EVENTS or not isinstance(entries, list):
                continue
            event_paths: list[Path] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                command = entry.get("command")
                if isinstance(command, str) and is_sidepulse_hook_command(command, "hermes"):
                    event_paths.extend(extract_log_paths_from_command(command))
            if event_paths:
                hook_events.append(event_name)
                paths.extend(event_paths)

    return ProviderConfig(
        "hermes",
        config_path,
        True,
        bool(hook_events),
        tuple(sorted(set(hook_events))),
        _dedupe_paths(paths),
    )


OPENCLAW_HOOK_NAME = "sidepulse-status"


def default_openclaw_config_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / ".openclaw" / "openclaw.json"


def openclaw_hook_dir(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / ".openclaw" / "hooks" / OPENCLAW_HOOK_NAME


def detect_openclaw_config(home: Path | None = None) -> ProviderConfig:
    """Installed-ness for OpenClaw means BOTH halves are present: the
    handler directory under ~/.openclaw/hooks/ AND the enabled entry in
    openclaw.json -- either alone does nothing (the gateway only loads
    enabled entries, and an entry without its handler is dead config)."""
    config_path = default_openclaw_config_path(home)
    if not config_path.exists():
        return ProviderConfig("openclaw", config_path, False, False, (), ())
    try:
        data = json.loads(config_path.read_text())
    except Exception:
        return ProviderConfig("openclaw", config_path, True, False, (), ())

    entry_enabled = False
    if isinstance(data, dict):
        internal = ((data.get("hooks") or {}).get("internal")) or {}
        if isinstance(internal, dict):
            entry = (internal.get("entries") or {}).get(OPENCLAW_HOOK_NAME)
            entry_enabled = bool(isinstance(entry, dict) and entry.get("enabled"))

    handler = openclaw_hook_dir(home) / "handler.ts"
    paths: list[Path] = []
    if handler.exists():
        try:
            # The handler passes args as a JS array, so the log path sits
            # in '"--log", "<path>"' form -- not shell syntax.
            for match in re.finditer(r'"--log",\s*"([^"]+)"', handler.read_text()):
                paths.append(Path(match.group(1)).expanduser())
        except OSError:
            paths = []

    installed = entry_enabled and handler.exists()
    return ProviderConfig(
        "openclaw",
        config_path,
        True,
        installed,
        OPENCLAW_EVENTS if installed else (),
        _dedupe_paths(paths),
    )


def default_antigravity_config_path(home: Path | None = None) -> Path:
    """Antigravity's GLOBAL customization root is ~/.gemini/config/.

    Not ~/.antigravity/ (that is only the editor's extension directory) and
    not the app bundle: the shipped documentation names `~/.gemini/config/`
    as the global customization root, and the same tree already holds the
    plugins/ directory that root is defined to contain.
    """
    base = home or Path.home()
    return base / ".gemini" / "config" / "hooks.json"


def _antigravity_handler_commands(entries: object) -> list[str]:
    """Pull SidePulse's own commands out of either hooks.json handler shape."""
    commands: list[str] = []
    if not isinstance(entries, list):
        return commands
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        # Grouped shape: {"matcher": ..., "hooks": [handler, ...]}.
        grouped = entry.get("hooks")
        handlers = grouped if isinstance(grouped, list) else [entry]
        for handler in handlers:
            if not isinstance(handler, dict):
                continue
            command = handler.get("command")
            if isinstance(command, str) and is_sidepulse_hook_command(command, "antigravity"):
                commands.append(command)
    return commands


def detect_antigravity_config(home: Path | None = None) -> ProviderConfig:
    """hooks.json is keyed by hook NAME, not by event.

    Every other provider's config nests events under a "hooks" object; here
    the top level is a map of named hooks, each of which then holds its own
    event keys. It is a shared user-level file -- other tools' named hooks
    and unknown keys must survive untouched -- so only SidePulse's own named
    entry, and only its own commands inside it, count toward installed-ness.
    """
    config_path = default_antigravity_config_path(home)
    if not config_path.exists():
        return ProviderConfig("antigravity", config_path, False, False, (), ())
    try:
        data = json.loads(config_path.read_text())
    except Exception:
        return ProviderConfig("antigravity", config_path, True, False, (), ())

    entry = data.get(ANTIGRAVITY_HOOK_NAME) if isinstance(data, dict) else None
    hook_events: list[str] = []
    paths: list[Path] = []
    if isinstance(entry, dict) and entry.get("enabled", True) is not False:
        for event_name in ANTIGRAVITY_EVENTS:
            event_paths: list[Path] = []
            for command in _antigravity_handler_commands(entry.get(event_name)):
                event_paths.extend(extract_log_paths_from_command(command))
            if event_paths:
                hook_events.append(event_name)
                paths.extend(event_paths)

    return ProviderConfig(
        "antigravity",
        config_path,
        True,
        bool(hook_events),
        tuple(sorted(set(hook_events))),
        _dedupe_paths(paths),
    )


def default_opencode_plugin_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / ".config" / "opencode" / "plugins" / "sidepulse.js"


def detect_opencode_plugin(home: Path | None = None) -> ProviderConfig:
    """Recognize only the exact SidePulse-managed OpenCode global plugin."""
    plugin_path = default_opencode_plugin_path(home)
    try:
        info = plugin_path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise OSError(f"refusing non-regular OpenCode plugin: {plugin_path}")
        if info.st_size > _OPENCODE_PLUGIN_MAX_SOURCE_BYTES:
            raise OSError(f"OpenCode plugin exceeds private read limit: {plugin_path}")
        text = read_private_text(
            plugin_path,
            tighten=False,
            max_bytes=_OPENCODE_PLUGIN_MAX_SOURCE_BYTES,
        )
        log_path = managed_opencode_plugin_log_path(text)
    except (FileNotFoundError, OSError, UnicodeError):
        log_path = None
    installed = log_path is not None
    return ProviderConfig(
        "opencode",
        plugin_path,
        installed,
        installed,
        OPENCODE_EVENTS if installed else (),
        (log_path,) if log_path is not None else (),
    )


def default_kiro_agent_config_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / ".kiro" / "agents" / "sidepulse.json"


def detect_kiro_config(home: Path | None = None) -> ProviderConfig:
    config_path = default_kiro_agent_config_path(home)
    if not config_path.exists():
        return ProviderConfig("kiro", config_path, False, False, (), ())
    try:
        data = json.loads(config_path.read_text())
    except Exception:
        return ProviderConfig("kiro", config_path, True, False, (), ())
    hooks = data.get("hooks") if isinstance(data, dict) else {}
    events: list[str] = []
    paths: list[Path] = []
    if isinstance(hooks, dict):
        for event_name, entries in hooks.items():
            canonical = canonical_event_name(event_name)
            if canonical not in KIRO_EVENTS or not isinstance(entries, list):
                continue
            event_paths: list[Path] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                command = entry.get("command")
                if isinstance(command, str) and is_sidepulse_hook_command(
                    command, "kiro"
                ):
                    event_paths.extend(extract_log_paths_from_command(command))
            if event_paths:
                events.append(canonical)
                paths.extend(event_paths)
    enabled = (
        isinstance(data, dict)
        and data.get("description") == KIRO_MANAGED_DESCRIPTION
        and bool(events)
    )
    return ProviderConfig(
        "kiro",
        config_path,
        True,
        enabled,
        tuple(sorted(set(events))),
        _dedupe_paths(paths),
    )


PROVIDER_SPECS = (
    ProviderSpec("codex", "Codex", CODEX_EVENTS, "codex-toml", default_codex_config_path, detect_codex_config),
    ProviderSpec("claude", "Claude", CLAUDE_EVENTS, "claude-json", default_claude_config_path, detect_claude_config),
    ProviderSpec("devin", "Devin", DEVIN_EVENTS, "devin-json", default_devin_config_path, detect_devin_config),
    ProviderSpec("grok", "Grok", GROK_EVENTS, "grok-json", default_grok_hook_config_path, detect_grok_config),
    ProviderSpec("cursor", "Cursor", CURSOR_EVENTS, "cursor-json", default_cursor_config_path, detect_cursor_config),
    ProviderSpec("hermes", "Hermes Agent", HERMES_EVENTS, "hermes-yaml", default_hermes_config_path, detect_hermes_config),
    ProviderSpec(
        "openclaw", "OpenClaw", OPENCLAW_EVENTS, "openclaw-handler", default_openclaw_config_path, detect_openclaw_config
    ),
    ProviderSpec(
        "opencode", "OpenCode", OPENCODE_EVENTS, "opencode-plugin", default_opencode_plugin_path, detect_opencode_plugin
    ),
    ProviderSpec(
        "antigravity",
        "Antigravity",
        ANTIGRAVITY_EVENTS,
        "antigravity-json",
        default_antigravity_config_path,
        detect_antigravity_config,
    ),
    ProviderSpec(
        "kiro",
        "Kiro",
        KIRO_EVENTS,
        "kiro-json",
        default_kiro_agent_config_path,
        detect_kiro_config,
    ),
)
PROVIDER_REGISTRY = {spec.provider: spec for spec in PROVIDER_SPECS}
HOOK_PROVIDERS = tuple(PROVIDER_REGISTRY)

_PROVIDER_SOURCE_REGISTRATIONS = (
    ProviderSourceRegistration(
        ProviderIdentifier("codex"),
        AdapterIdentifier("hooks"),
        SourceInstanceIdentifier("global"),
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        (
            (
                CapabilityIdentifier("live_agent_events"),
                (SchemaVersion(1, 0), SchemaVersion(1, 1)),
            ),
            (
                CapabilityIdentifier("actionable_requests"),
                (SchemaVersion(1, 0),),
            ),
        ),
    ),
    ProviderSourceRegistration(
        ProviderIdentifier("codex"),
        AdapterIdentifier("transcripts"),
        SourceInstanceIdentifier("local"),
        ObservationAuthority.FALLBACK_OBSERVATION,
        (
            (
                CapabilityIdentifier("transcript_usage"),
                (SchemaVersion(1, 0),),
            ),
        ),
    ),
    ProviderSourceRegistration(
        ProviderIdentifier("codex"),
        AdapterIdentifier("quota"),
        SourceInstanceIdentifier("local"),
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        (
            (
                CapabilityIdentifier("remote_quota_windows"),
                (SchemaVersion(1, 0),),
            ),
        ),
    ),
    ProviderSourceRegistration(
        ProviderIdentifier("claude"),
        AdapterIdentifier("hooks"),
        SourceInstanceIdentifier("global"),
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        (
            (
                CapabilityIdentifier("live_agent_events"),
                (SchemaVersion(1, 0), SchemaVersion(1, 1)),
            ),
            (
                CapabilityIdentifier("actionable_requests"),
                (SchemaVersion(1, 0),),
            ),
        ),
    ),
    ProviderSourceRegistration(
        ProviderIdentifier("claude"),
        AdapterIdentifier("transcripts"),
        SourceInstanceIdentifier("local"),
        ObservationAuthority.FALLBACK_OBSERVATION,
        (
            (
                CapabilityIdentifier("transcript_usage"),
                (SchemaVersion(1, 0),),
            ),
        ),
    ),
    # `oauth`, not `local`: this instance is the remote subscription endpoint
    # read with Claude Code's own credential, and the instance id is what
    # keeps its retry state separate from claude/transcripts/local.
    ProviderSourceRegistration(
        ProviderIdentifier("claude"),
        AdapterIdentifier("quota"),
        SourceInstanceIdentifier("oauth"),
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        (
            (
                CapabilityIdentifier("remote_quota_windows"),
                (SchemaVersion(1, 0),),
            ),
        ),
    ),
    ProviderSourceRegistration(
        ProviderIdentifier("devin"),
        AdapterIdentifier("hooks"),
        SourceInstanceIdentifier("global"),
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        (
            (
                CapabilityIdentifier("live_agent_events"),
                (SchemaVersion(1, 0), SchemaVersion(1, 1)),
            ),
            (
                CapabilityIdentifier("actionable_requests"),
                (SchemaVersion(1, 0),),
            ),
        ),
    ),
    ProviderSourceRegistration(
        ProviderIdentifier("grok"),
        AdapterIdentifier("hooks"),
        SourceInstanceIdentifier("global"),
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        (
            (
                CapabilityIdentifier("live_agent_events"),
                (SchemaVersion(1, 0), SchemaVersion(1, 1)),
            ),
            (
                CapabilityIdentifier("actionable_requests"),
                (SchemaVersion(1, 0),),
            ),
        ),
    ),
    ProviderSourceRegistration(
        ProviderIdentifier("cursor"),
        AdapterIdentifier("hooks"),
        SourceInstanceIdentifier("global"),
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        (
            (
                CapabilityIdentifier("live_agent_events"),
                (SchemaVersion(1, 0), SchemaVersion(1, 1)),
            ),
        ),
    ),
    ProviderSourceRegistration(
        ProviderIdentifier("hermes"),
        AdapterIdentifier("hooks"),
        SourceInstanceIdentifier("global"),
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        (
            (
                CapabilityIdentifier("live_agent_events"),
                (SchemaVersion(1, 0), SchemaVersion(1, 1)),
            ),
        ),
    ),
    ProviderSourceRegistration(
        ProviderIdentifier("openclaw"),
        AdapterIdentifier("hooks"),
        SourceInstanceIdentifier("global"),
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        (
            (
                CapabilityIdentifier("live_agent_events"),
                (SchemaVersion(1, 0), SchemaVersion(1, 1)),
            ),
        ),
    ),
    ProviderSourceRegistration(
        ProviderIdentifier("opencode"),
        AdapterIdentifier("hooks"),
        SourceInstanceIdentifier("global"),
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        (
            (
                CapabilityIdentifier("live_agent_events"),
                (SchemaVersion(1, 0), SchemaVersion(1, 1)),
            ),
            (
                CapabilityIdentifier("actionable_requests"),
                (SchemaVersion(1, 0),),
            ),
        ),
    ),
    # live_agent_events only. NO actionable_requests: the one Antigravity
    # event that carries a user-facing decision is PreToolUse, which SidePulse
    # refuses to install (see ANTIGRAVITY_EVENTS), so nothing in this feed can
    # ever name a live request. Declaring the capability anyway would let the
    # request lane look supported and permanently empty.
    ProviderSourceRegistration(
        ProviderIdentifier("antigravity"),
        AdapterIdentifier("hooks"),
        SourceInstanceIdentifier("global"),
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        (
            (
                CapabilityIdentifier("live_agent_events"),
                (SchemaVersion(1, 0), SchemaVersion(1, 1)),
            ),
        ),
    ),
    # live_agent_events only: Kiro's hook set carries no ask-shaped event
    # (no PermissionRequest), so actionable_requests would be a lane that
    # looks supported and stays permanently empty.
    ProviderSourceRegistration(
        ProviderIdentifier("kiro"),
        AdapterIdentifier("hooks"),
        SourceInstanceIdentifier("global"),
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        (
            (
                CapabilityIdentifier("live_agent_events"),
                (SchemaVersion(1, 0), SchemaVersion(1, 1)),
            ),
        ),
    ),
)


def provider_source_registrations() -> tuple[ProviderSourceRegistration, ...]:
    """Return the immutable, import-time first-party source declarations."""
    return _PROVIDER_SOURCE_REGISTRATIONS


def provider_capacity_source_registrations() -> tuple[ProviderSourceRegistration, ...]:
    """Return only exact provider sources that declare capacity observation."""
    capacity_capability = CapabilityIdentifier("remote_quota_windows")
    return tuple(
        registration
        for registration in _PROVIDER_SOURCE_REGISTRATIONS
        if any(
            capability_id == capacity_capability
            for capability_id, _versions in registration.capability_versions
        )
    )


def negotiated_provider_sources() -> tuple[NegotiatedProviderSource, ...]:
    """Negotiate one visible canonical row per declared read capability."""
    rows: list[NegotiatedProviderSource] = []
    for registration in _PROVIDER_SOURCE_REGISTRATIONS:
        contract = negotiate_provider_contract(provider_contract_document(registration))
        negotiated_by_id = {
            capability.identifier: capability
            for capability in (
                *contract.discovery_capabilities,
                *contract.observation_capabilities,
            )
        }
        for capability_id, versions in registration.capability_versions:
            rows.append(
                NegotiatedProviderSource(
                    source_key=SourceKey(
                        registration.provider_id.value,
                        registration.adapter_id.value,
                        registration.source_instance_id.value,
                        capability_id.value,
                    ),
                    registration=registration,
                    contract=contract,
                    declared_capability_id=capability_id,
                    declared_capability_version=max(versions),
                    negotiated_capability=negotiated_by_id.get(capability_id),
                )
            )
    return tuple(rows)


def sources_with_capability(
    sources: tuple[NegotiatedProviderSource, ...],
    capability_id: CapabilityIdentifier,
) -> tuple[NegotiatedProviderSource, ...]:
    """Return exact capability rows that passed read-side negotiation."""
    return tuple(
        source
        for source in sources
        if source.declared_capability_id == capability_id
        and source.observation_invocation_allowed
    )

# The CANONICAL event vocabulary -- what everything normalizes TO. Cursor,
# Hermes and Antigravity register their own native names in their configs
# (their spec .events tuples), but those must never enter this set:
# canonical_event_name returns members of this set verbatim, so a native name
# here would leak through ingest un-normalized and break mode mapping
# downstream. ANTIGRAVITY_EVENTS is absent for exactly that reason -- its
# PreInvocation is a config key, never an ingested event name, and a hand
# written hook that forwarded the literal name is dropped rather than guessed.
KNOWN_EVENTS = tuple(
    dict.fromkeys(
        event
        for events in (
            CODEX_EVENTS,
            CLAUDE_EVENTS,
            GROK_EVENTS,
            DEVIN_EVENTS,
            OPENCLAW_EVENTS,
            OPENCODE_EVENTS,
        )
        for event in events
    )
)


def provider_spec(provider: str) -> ProviderSpec:
    try:
        return PROVIDER_REGISTRY[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported hook provider: {provider}") from exc


def detect_provider_configs(home: Path | None = None) -> list[ProviderConfig]:
    return [spec.detector(home) for spec in PROVIDER_SPECS]


def detect_log_path(provider: str, home: Path | None = None) -> Path:
    try:
        config = provider_spec(provider).detector(home)
    except ValueError:
        config = ProviderConfig(provider, default_log_path(provider, home), False, False, (), ())
    if config.log_paths:
        return config.log_paths[0]
    return default_log_path(provider, home)


def parse_log_line(provider: str, line: str) -> HookEvent | None:
    line = line.strip()
    if not line:
        return None

    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None

    if not isinstance(obj, dict):
        return None

    if provider == "codex" and isinstance(obj.get("event"), dict):
        raw = obj["event"]
        logged_at = obj.get("logged_at") or raw.get("logged_at")
    else:
        raw = obj
        logged_at = raw.get("logged_at") or raw.get("timestamp")
    provider = infer_provider_from_payload(provider, raw)

    event_name = canonical_event_name(
        raw.get("hook_event_name")
        or raw.get("hookEventName")
        or raw.get("event_name")
        or raw.get("eventName")
    )
    if not event_name:
        return None

    normalized_raw = normalize_event_payload(raw, event_name, logged_at)

    return HookEvent(
        provider=provider,
        logged_at=parse_datetime(logged_at),
        event_name=event_name,
        raw=normalized_raw,
        session_id=_first_string(normalized_raw, "session_id", "sessionId"),
        turn_id=_first_string(normalized_raw, "turn_id", "turnId"),
        agent_id=_first_string(normalized_raw, "agent_id", "agentId"),
        cwd=_first_string(normalized_raw, "cwd", "workspaceRoot"),
        tool_name=_first_string(normalized_raw, "tool_name", "toolName"),
        message=_first_string(normalized_raw, "message", "last_assistant_message", "lastAssistantMessage"),
        origin=origin_label_from_payload(provider, normalized_raw),
    )


def infer_provider_from_payload(provider: str, raw: dict[str, Any]) -> str:
    if provider == "claude" and grok_payload_looks_compatible(raw):
        return "grok"
    return provider


def grok_payload_looks_compatible(raw: dict[str, Any]) -> bool:
    transcript_path = str(raw.get("transcriptPath") or raw.get("transcript_path") or "")
    if "/.grok/" in transcript_path or "\\.grok\\" in transcript_path:
        return True

    camel_grok_keys = {"hookEventName", "sessionId", "workspaceRoot"}
    return "hookEventName" in raw and bool(camel_grok_keys.intersection(raw))


def canonical_event_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text == "PostCompaction":
        return "PostCompact"
    if text in KNOWN_EVENTS:
        return text

    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", text)
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized)
    normalized = normalized.strip("_").lower()
    aliases = {_event_alias_key(event): event for event in KNOWN_EVENTS}
    aliases.update(
        {
            "pre_tool_use": "PreToolUse",
            "post_tool_use": "PostToolUse",
            "post_tool_use_failure": "PostToolUseFailure",
            "permission_request": "PermissionRequest",
            "permission_denied": "PermissionDenied",
            "user_prompt_submit": "UserPromptSubmit",
            "session_start": "SessionStart",
            "session_end": "SessionEnd",
            "subagent_start": "SubagentStart",
            "subagent_stop": "SubagentStop",
            "subagent_end": "SubagentStop",
            "pre_compact": "PreCompact",
            "post_compact": "PostCompact",
            "post_compaction": "PostCompact",
            # Kiro natives (camelCase normalizes to snake_case first).
            "agent_spawn": "SessionStart",
            "stop_failure": "StopFailure",
            # Cursor natives (camelCase normalizes to snake_case first).
            "before_shell_execution": "PreToolUse",
            "after_shell_execution": "PostToolUse",
            "before_mcp_execution": "PreToolUse",
            "after_mcp_execution": "PostToolUse",
            "before_submit_prompt": "UserPromptSubmit",
            # Hermes Agent natives.
            "pre_tool_call": "PreToolUse",
            "post_tool_call": "PostToolUse",
            "pre_llm_call": "UserPromptSubmit",
            "on_session_start": "SessionStart",
            "on_session_end": "HermesTurnEnd",
            "on_session_finalize": "SessionFinalize",
            "api_request_error": "ApiRequestError",
        }
    )
    return aliases.get(normalized)


def normalize_event_payload(raw: dict[str, Any], event_name: str, logged_at: Any) -> dict[str, Any]:
    normalized = dict(raw)
    normalized.setdefault("hook_event_name", event_name)
    if logged_at is not None:
        normalized.setdefault("logged_at", logged_at)

    _copy_alias(normalized, "sessionId", "session_id")
    _copy_alias(normalized, "turnId", "turn_id")
    _copy_alias(normalized, "prompt_id", "turn_id")
    _copy_alias(normalized, "agentId", "agent_id")
    _copy_alias(normalized, "workspaceRoot", "cwd")
    _copy_alias(normalized, "toolName", "tool_name")
    _copy_alias(normalized, "toolInput", "tool_input")
    _copy_alias(normalized, "toolResponse", "tool_response")
    _copy_alias(normalized, "lastAssistantMessage", "last_assistant_message")
    _copy_alias(normalized, "notificationType", "notification_type")
    _copy_alias(normalized, "agentOrigin", "agent_origin")
    _copy_alias(normalized, "agentOriginKind", "agent_origin_kind")
    _copy_alias(normalized, "sidepulseOrigin", "sidepulse_origin")
    return normalized


def _event_alias_key(event_name: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", event_name).lower()


def _copy_alias(data: dict[str, Any], source: str, target: str) -> None:
    if target not in data and source in data:
        data[target] = data[source]


def _paths_from_hook_entries(
    entries: list[Any],
    command_filter: Callable[[str], bool] | None = None,
) -> list[Path]:
    paths: list[Path] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for hook in entry.get("hooks") or []:
            if not isinstance(hook, dict):
                continue
            command = hook.get("command")
            if isinstance(command, str) and (command_filter is None or command_filter(command)):
                paths.extend(extract_log_paths_from_command(command))
    return paths


def is_sidepulse_devin_command(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return False

    has_devin_provider = any(
        (part == "--provider" and index + 1 < len(parts) and parts[index + 1] == "devin")
        or part == "--provider=devin"
        for index, part in enumerate(parts)
    )
    if not has_devin_provider:
        return False

    return _is_sidepulse_hook_invocation(parts)


def extract_log_paths_from_command(command: str) -> list[Path]:
    paths: list[Path] = []

    for match in re.finditer(r">>\s+(['\"]?)([^'\"\s]+)\1", command):
        paths.append(Path(match.group(2)).expanduser())

    try:
        parts = shlex.split(command)
    except ValueError:
        parts = []

    for index, part in enumerate(parts):
        if part == "--log" and index + 1 < len(parts):
            paths.append(Path(parts[index + 1].rstrip(";")).expanduser())
        elif part.startswith("--log="):
            paths.append(Path(part.split("=", 1)[1].rstrip(";")).expanduser())

    return _dedupe_paths(paths)


def _dedupe_paths(paths: list[Path]) -> tuple[Path, ...]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return tuple(result)


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _first_string(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _string_or_none(data.get(key))
        if value:
            return value
    return None
