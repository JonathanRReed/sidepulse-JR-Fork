from __future__ import annotations

import json
import os
import queue
import re
import shutil
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .providers import (
    CLAUDE_EVENTS,
    CODEX_EVENTS,
    CURSOR_EVENTS,
    DEVIN_EVENTS,
    GROK_EVENTS,
    HERMES_EVENTS,
    OPENCLAW_HOOK_NAME,
    default_cursor_config_path,
    default_devin_config_path,
    default_grok_hook_config_path,
    default_hermes_config_path,
    default_openclaw_config_path,
    detect_log_path,
    is_sidepulse_hook_command,
    openclaw_hook_dir,
)

MANAGED_START = "# >>> agent-monitor hooks >>>"
MANAGED_END = "# <<< agent-monitor hooks <<<"


@dataclass(frozen=True)
class InstallResult:
    provider: str
    config_path: Path
    log_path: Path
    changed: bool
    backup_path: Path | None = None
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "config_path": str(self.config_path),
            "log_path": str(self.log_path),
            "changed": self.changed,
            "backup_path": str(self.backup_path) if self.backup_path else None,
            "dry_run": self.dry_run,
        }


def install_codex_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    config = config_path or Path.home() / ".codex" / "config.toml"
    target_log = (log_path or detect_log_path("codex")).expanduser()
    original = config.read_text() if config.exists() else ""

    block = codex_hook_block(target_log, python_executable)
    if is_pristine_codex_hook_install(original, block, target_log):
        new_text = original
    else:
        text = strip_managed_block(original)
        text = remove_codex_hook_blocks_for_log(text, target_log)
        text = ensure_codex_hooks_feature(text)
        new_text = _ensure_trailing_newline(text) + "\n" + block
    changed = new_text != original

    backup = None
    if not dry_run:
        config.parent.mkdir(parents=True, exist_ok=True)
        if changed:
            backup = backup_file(config)
            config.write_text(new_text)

        if should_refresh_codex_hook_trust(config, config_path):
            trusted_hashes = resolve_codex_hook_hashes(config)
            if trusted_hashes:
                current_text = config.read_text() if config.exists() else ""
                trusted_text = update_codex_trusted_hashes(current_text, trusted_hashes)
                if trusted_text != current_text:
                    if backup is None:
                        backup = backup_file(config)
                    config.write_text(trusted_text)
                    changed = True

        target_log.parent.mkdir(parents=True, exist_ok=True)
        target_log.touch(exist_ok=True)

    return InstallResult("codex", config, target_log, changed, backup, dry_run)


def install_claude_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    config = config_path or Path.home() / ".claude" / "settings.json"
    target_log = (log_path or detect_log_path("claude")).expanduser()

    if config.exists():
        data = json.loads(config.read_text())
    else:
        data = {}

    original = json.dumps(data, sort_keys=True)
    hooks = data.setdefault("hooks", {})
    command = hook_command("claude", target_log, python_executable)

    for event_name in CLAUDE_EVENTS:
        entries = hooks.get(event_name, [])
        if not isinstance(entries, list):
            entries = []
        cleaned = remove_claude_hooks_for_log(entries, target_log)
        cleaned.append({"matcher": "*", "hooks": [{"type": "command", "command": command}]})
        hooks[event_name] = cleaned

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        config.parent.mkdir(parents=True, exist_ok=True)
        backup = backup_file(config)
        config.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
        target_log.parent.mkdir(parents=True, exist_ok=True)
        target_log.touch(exist_ok=True)

    return InstallResult("claude", config, target_log, changed, backup, dry_run)


def install_grok_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    config = config_path or default_grok_hook_config_path()
    target_log = (log_path or detect_log_path("grok")).expanduser()
    data = read_json_config(config)

    original = json.dumps(data, sort_keys=True)
    hooks = data.setdefault("hooks", {})
    command = hook_command("grok", target_log, python_executable)

    for event_name in GROK_EVENTS:
        entries = hooks.get(event_name, [])
        if not isinstance(entries, list):
            entries = []
        cleaned = remove_json_command_hooks_for_log(entries, target_log, "grok")
        cleaned.append(grok_hook_entry(event_name, command))
        hooks[event_name] = cleaned

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        config.parent.mkdir(parents=True, exist_ok=True)
        backup = backup_file(config)
        config.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
        target_log.parent.mkdir(parents=True, exist_ok=True)
        target_log.touch(exist_ok=True)

    return InstallResult("grok", config, target_log, changed, backup, dry_run)


def install_devin_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    config = config_path or default_devin_config_path()
    target_log = (log_path or detect_log_path("devin")).expanduser()
    data = read_json_config(config)

    original = json.dumps(data, sort_keys=True)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"Expected hooks object in {config}")
    command = hook_command("devin", target_log, python_executable)
    for event_name in DEVIN_EVENTS:
        entries = hooks.get(event_name, [])
        if not isinstance(entries, list):
            raise ValueError(f"Expected hooks.{event_name} array in {config}")
        cleaned = remove_json_command_hooks_for_log(entries, target_log, "devin")
        cleaned.append({"hooks": [{"type": "command", "command": command}]})
        hooks[event_name] = cleaned

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        config.parent.mkdir(parents=True, exist_ok=True)
        backup = backup_file(config)
        config.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
        target_log.parent.mkdir(parents=True, exist_ok=True)
        target_log.touch(exist_ok=True)

    return InstallResult("devin", config, target_log, changed, backup, dry_run)


def _remove_flat_sidepulse_hooks(entries: list[Any], provider: str) -> list[Any]:
    """Drops SidePulse's own flat {"command": ...} hook entries, keeping
    everything else byte-identical -- for configs (Cursor, Hermes) whose
    hook entries hold the command directly rather than Claude's nested
    {"hooks": [...]} shape."""
    cleaned: list[Any] = []
    for entry in entries:
        if isinstance(entry, dict):
            command = entry.get("command")
            if isinstance(command, str) and is_sidepulse_hook_command(command, provider):
                continue
        cleaned.append(entry)
    return cleaned


def install_cursor_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    """Adds SidePulse's command to ~/.cursor/hooks.json (shared user-level
    file: other tools' hooks and unknown keys are preserved untouched)."""
    config = config_path or default_cursor_config_path()
    target_log = (log_path or detect_log_path("cursor")).expanduser()
    data = read_json_config(config)

    original = json.dumps(data, sort_keys=True)
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"Expected hooks object in {config}")
    command = hook_command("cursor", target_log, python_executable)

    for event_name in CURSOR_EVENTS:
        entries = hooks.get(event_name, [])
        if not isinstance(entries, list):
            entries = []
        cleaned = _remove_flat_sidepulse_hooks(entries, "cursor")
        cleaned.append({"command": command})
        hooks[event_name] = cleaned

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        config.parent.mkdir(parents=True, exist_ok=True)
        backup = backup_file(config)
        config.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
        target_log.parent.mkdir(parents=True, exist_ok=True)
        target_log.touch(exist_ok=True)

    return InstallResult("cursor", config, target_log, changed, backup, dry_run)


def uninstall_cursor_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    config = config_path or default_cursor_config_path()
    target_log = (log_path or detect_log_path("cursor")).expanduser()
    data = read_json_config(config)

    original = json.dumps(data, sort_keys=True)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event_name in list(hooks):
            entries = hooks.get(event_name)
            if event_name not in CURSOR_EVENTS or not isinstance(entries, list):
                continue
            cleaned = _remove_flat_sidepulse_hooks(entries, "cursor")
            if cleaned:
                hooks[event_name] = cleaned
            else:
                hooks.pop(event_name, None)
        if not hooks:
            data.pop("hooks", None)

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        config.parent.mkdir(parents=True, exist_ok=True)
        backup = backup_file(config)
        config.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")

    return InstallResult("cursor", config, target_log, changed, backup, dry_run)


def _hermes_yaml():
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    return yaml


def _hermes_dump(yaml, data) -> str:
    import io

    buffer = io.StringIO()
    yaml.dump(data, buffer)
    return buffer.getvalue()


def install_hermes_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    """Adds SidePulse's shell hooks to ~/.hermes/config.yaml's hooks:
    block. Edited with a round-trip YAML parser (ruamel) specifically so
    the user's own comments and formatting survive -- config.yaml is a
    hand-maintained file for most Hermes users."""
    config = config_path or default_hermes_config_path()
    target_log = (log_path or detect_log_path("hermes")).expanduser()
    yaml = _hermes_yaml()
    if config.exists():
        data = yaml.load(config.read_text())
        if data is None:
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping at the top level of {config}")

    original = _hermes_dump(yaml, data)
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
        data["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise ValueError(f"Expected hooks mapping in {config}")
    command = hook_command("hermes", target_log, python_executable)

    for event_name in HERMES_EVENTS:
        entries = hooks.get(event_name)
        if not isinstance(entries, list):
            entries = []
        cleaned = _remove_flat_sidepulse_hooks(list(entries), "hermes")
        # Hooks time out per invocation; ours just appends a JSON line,
        # so a tight timeout keeps a wedged filesystem from ever
        # stalling the agent loop.
        cleaned.append({"command": command, "timeout": 10})
        hooks[event_name] = cleaned

    changed = _hermes_dump(yaml, data) != original
    backup = None
    if changed and not dry_run:
        config.parent.mkdir(parents=True, exist_ok=True)
        backup = backup_file(config)
        config.write_text(_hermes_dump(yaml, data))
        target_log.parent.mkdir(parents=True, exist_ok=True)
        target_log.touch(exist_ok=True)

    return InstallResult("hermes", config, target_log, changed, backup, dry_run)


def uninstall_hermes_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    config = config_path or default_hermes_config_path()
    target_log = (log_path or detect_log_path("hermes")).expanduser()
    yaml = _hermes_yaml()
    if config.exists():
        data = yaml.load(config.read_text())
        if data is None:
            data = {}
    else:
        data = {}
    if not isinstance(data, dict):
        return InstallResult("hermes", config, target_log, False, None, dry_run)

    original = _hermes_dump(yaml, data)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event_name in list(hooks):
            entries = hooks.get(event_name)
            if event_name not in HERMES_EVENTS or not isinstance(entries, list):
                continue
            cleaned = _remove_flat_sidepulse_hooks(list(entries), "hermes")
            if cleaned:
                hooks[event_name] = cleaned
            else:
                del hooks[event_name]
        if not hooks:
            data.pop("hooks", None)

    changed = _hermes_dump(yaml, data) != original
    backup = None
    if changed and not dry_run:
        config.parent.mkdir(parents=True, exist_ok=True)
        backup = backup_file(config)
        config.write_text(_hermes_dump(yaml, data))

    return InstallResult("hermes", config, target_log, changed, backup, dry_run)


def openclaw_handler_source(log_path: Path, python_executable: str | None = None) -> str:
    """The in-gateway JS handler OpenClaw loads for SidePulse. It maps the
    gateway's own events to SidePulse's canonical hook events and forwards
    each as one short-lived detached process -- OpenClaw's hook contract
    forbids handlers owning long-lived resources, so each event is
    fire-and-forget."""
    executable = python_executable or sys.executable or "python3"
    entry_point = Path(__file__).with_name("hook_entry.py")
    return f"""// Managed by SidePulse -- reinstalling overwrites this file.
import {{ spawn }} from "node:child_process";

const EVENT_MAP = {{
  "command:new": "SessionStart",
  "message:received": "UserPromptSubmit",
  "message:sent": "Stop",
  "command:stop": "SessionEnd",
}};

const handler = async (event) => {{
  const mapped = EVENT_MAP[`${{event.type}}:${{event.action}}`];
  if (!mapped) return;
  const payload = JSON.stringify({{
    hook_event_name: mapped,
    session_id: event.sessionKey ?? null,
    logged_at: new Date().toISOString(),
  }});
  try {{
    const child = spawn(
      {json.dumps(str(executable))},
      [{json.dumps(str(entry_point))}, "--provider", "openclaw", "--log", {json.dumps(str(log_path.expanduser()))}],
      {{ stdio: ["pipe", "ignore", "ignore"], detached: true }},
    );
    child.stdin.end(payload);
    child.unref();
  }} catch {{}}
}};

export default handler;
"""


OPENCLAW_HOOK_MD = """---
name: {name}
description: "Forwards agent activity to SidePulse so the LEDs show live status."
metadata:
  openclaw:
    emoji: "\U0001F4A1"
    events: ["command:new", "command:stop", "message:received", "message:sent"]
    export: "default"
---

# SidePulse Status

Forwards OpenClaw gateway events to the SidePulse agent monitor. Managed
by SidePulse -- `sidepulse agent-monitor uninstall openclaw` removes it.
"""


def install_openclaw_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
    python_executable: str | None = None,
) -> InstallResult:
    """Two coordinated writes: the handler directory under
    ~/.openclaw/hooks/ (auto-discovered by the gateway) and the enabled
    entry in openclaw.json. Unknown config keys are preserved untouched;
    the gateway needs a restart to pick the hook up."""
    config = config_path or default_openclaw_config_path()
    target_log = (log_path or detect_log_path("openclaw")).expanduser()
    hook_dir = openclaw_hook_dir() if config_path is None else config.parent / "hooks" / OPENCLAW_HOOK_NAME
    data = read_json_config(config)

    original = json.dumps(data, sort_keys=True)
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"Expected hooks object in {config}")
    internal = hooks.setdefault("internal", {})
    if not isinstance(internal, dict):
        raise ValueError(f"Expected hooks.internal object in {config}")
    internal["enabled"] = True
    entries = internal.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise ValueError(f"Expected hooks.internal.entries object in {config}")
    entries[OPENCLAW_HOOK_NAME] = {"enabled": True}

    handler_source = openclaw_handler_source(target_log, python_executable)
    handler_path = hook_dir / "handler.ts"
    hook_md_path = hook_dir / "HOOK.md"
    files_changed = (
        not handler_path.exists()
        or handler_path.read_text() != handler_source
        or not hook_md_path.exists()
    )

    changed = json.dumps(data, sort_keys=True) != original or files_changed
    backup = None
    if changed and not dry_run:
        hook_dir.mkdir(parents=True, exist_ok=True)
        handler_path.write_text(handler_source)
        hook_md_path.write_text(OPENCLAW_HOOK_MD.format(name=OPENCLAW_HOOK_NAME))
        config.parent.mkdir(parents=True, exist_ok=True)
        backup = backup_file(config)
        config.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
        target_log.parent.mkdir(parents=True, exist_ok=True)
        target_log.touch(exist_ok=True)

    return InstallResult("openclaw", config, target_log, changed, backup, dry_run)


def uninstall_openclaw_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    """Removes only SidePulse's own entry and handler directory --
    hooks.internal.enabled stays as-is (other entries may rely on it)."""
    config = config_path or default_openclaw_config_path()
    target_log = (log_path or detect_log_path("openclaw")).expanduser()
    hook_dir = openclaw_hook_dir() if config_path is None else config.parent / "hooks" / OPENCLAW_HOOK_NAME
    data = read_json_config(config)

    original = json.dumps(data, sort_keys=True)
    internal = (data.get("hooks") or {}).get("internal")
    if isinstance(internal, dict):
        entries = internal.get("entries")
        if isinstance(entries, dict):
            entries.pop(OPENCLAW_HOOK_NAME, None)
            if not entries:
                internal.pop("entries", None)

    changed = json.dumps(data, sort_keys=True) != original or hook_dir.exists()
    backup = None
    if changed and not dry_run:
        if json.dumps(data, sort_keys=True) != original:
            config.parent.mkdir(parents=True, exist_ok=True)
            backup = backup_file(config)
            config.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
        if hook_dir.exists():
            shutil.rmtree(hook_dir, ignore_errors=True)

    return InstallResult("openclaw", config, target_log, changed, backup, dry_run)


def uninstall_codex_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    config = config_path or Path.home() / ".codex" / "config.toml"
    target_log = (log_path or detect_log_path("codex")).expanduser()
    original = config.read_text() if config.exists() else ""

    text = strip_managed_block(original)
    text = remove_codex_hook_blocks_for_log(text, target_log)
    new_text = _normalize_config_text(text) if text != original else original
    changed = new_text != original

    backup = None
    if changed and not dry_run:
        config.parent.mkdir(parents=True, exist_ok=True)
        backup = backup_file(config)
        config.write_text(new_text)

    return InstallResult("codex", config, target_log, changed, backup, dry_run)


def uninstall_claude_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    config = config_path or Path.home() / ".claude" / "settings.json"
    target_log = (log_path or detect_log_path("claude")).expanduser()

    if config.exists():
        data = json.loads(config.read_text())
    else:
        data = {}

    original = json.dumps(data, sort_keys=True)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event_name in list(hooks):
            entries = hooks.get(event_name)
            if event_name not in CLAUDE_EVENTS or not isinstance(entries, list):
                continue

            cleaned = remove_claude_hooks_for_log(entries, target_log)
            if cleaned:
                hooks[event_name] = cleaned
            else:
                hooks.pop(event_name, None)

        if not hooks:
            data.pop("hooks", None)

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        config.parent.mkdir(parents=True, exist_ok=True)
        backup = backup_file(config)
        config.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")

    return InstallResult("claude", config, target_log, changed, backup, dry_run)


def uninstall_grok_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    config = config_path or default_grok_hook_config_path()
    target_log = (log_path or detect_log_path("grok")).expanduser()
    data = read_json_config(config)

    original = json.dumps(data, sort_keys=True)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event_name in list(hooks):
            entries = hooks.get(event_name)
            if event_name not in GROK_EVENTS or not isinstance(entries, list):
                continue

            cleaned = remove_json_command_hooks_for_log(entries, target_log, "grok")
            if cleaned:
                hooks[event_name] = cleaned
            else:
                hooks.pop(event_name, None)

        if not hooks:
            data.pop("hooks", None)

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        config.parent.mkdir(parents=True, exist_ok=True)
        backup = backup_file(config)
        if data:
            config.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
        else:
            try:
                config.unlink()
            except FileNotFoundError:
                pass

    return InstallResult("grok", config, target_log, changed, backup, dry_run)


def uninstall_devin_hooks(
    log_path: Path | None = None,
    config_path: Path | None = None,
    dry_run: bool = False,
) -> InstallResult:
    config = config_path or default_devin_config_path()
    target_log = (log_path or detect_log_path("devin")).expanduser()
    data = read_json_config(config)

    original = json.dumps(data, sort_keys=True)
    hooks = data.get("hooks")
    if isinstance(hooks, dict):
        for event_name in list(hooks):
            entries = hooks.get(event_name)
            if event_name not in DEVIN_EVENTS or not isinstance(entries, list):
                continue

            cleaned = remove_json_command_hooks_for_log(entries, target_log, "devin")
            if cleaned:
                hooks[event_name] = cleaned
            else:
                hooks.pop(event_name, None)

        if not hooks:
            data.pop("hooks", None)

    changed = json.dumps(data, sort_keys=True) != original
    backup = None
    if changed and not dry_run:
        config.parent.mkdir(parents=True, exist_ok=True)
        backup = backup_file(config)
        config.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")

    return InstallResult("devin", config, target_log, changed, backup, dry_run)


INSTALLERS = {
    "codex": install_codex_hooks,
    "claude": install_claude_hooks,
    "devin": install_devin_hooks,
    "grok": install_grok_hooks,
    "cursor": install_cursor_hooks,
    "hermes": install_hermes_hooks,
    "openclaw": install_openclaw_hooks,
}

UNINSTALLERS = {
    "codex": uninstall_codex_hooks,
    "claude": uninstall_claude_hooks,
    "devin": uninstall_devin_hooks,
    "grok": uninstall_grok_hooks,
    "cursor": uninstall_cursor_hooks,
    "hermes": uninstall_hermes_hooks,
    "openclaw": uninstall_openclaw_hooks,
}


def install_provider_hooks(provider: str, **kwargs: Any) -> InstallResult:
    return INSTALLERS[provider](**kwargs)


def uninstall_provider_hooks(provider: str, **kwargs: Any) -> InstallResult:
    return UNINSTALLERS[provider](**kwargs)


def hook_command(
    provider: str,
    log_path: Path,
    python_executable: str | None = None,
) -> str:
    executable = python_executable or sys.executable or "python3"
    if getattr(sys, "frozen", False) and python_executable is None:
        return " ".join(
            [
                shlex.quote(executable),
                "agent-monitor",
                "hook-log",
                "--provider",
                shlex.quote(provider),
                "--log",
                shlex.quote(str(log_path.expanduser())),
            ]
        )
    entry_point = Path(__file__).with_name("hook_entry.py")
    command = " ".join(
        [
            shlex.quote(executable),
            shlex.quote(str(entry_point)),
            "--provider",
            shlex.quote(provider),
            "--log",
            shlex.quote(str(log_path.expanduser())),
        ]
    )
    return command


def read_json_config(config: Path) -> dict[str, Any]:
    if not config.exists():
        return {}
    data = json.loads(config.read_text())
    return data if isinstance(data, dict) else {}


def grok_hook_entry(event_name: str, command: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"hooks": [{"type": "command", "command": command}]}
    if event_name in {"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionDenied", "Notification"}:
        entry["matcher"] = "*"
    return entry


def hook_pythonpath_assignment() -> str:
    package_root = Path(__file__).resolve().parents[1]
    if not package_root.exists():
        return ""
    return f"PYTHONPATH={shlex.quote(str(package_root))} "


def codex_hook_block(
    log_path: Path,
    python_executable: str | None = None,
) -> str:
    command = hook_command("codex", log_path, python_executable)
    lines = [
        MANAGED_START,
        "# Provider-neutral status collection. Do not edit inside this block.",
    ]
    for event_name in CODEX_EVENTS:
        lines.extend(
            [
                f"[[hooks.{event_name}]]",
                'matcher = "*"',
                f"[[hooks.{event_name}.hooks]]",
                'type = "command"',
                f"command = '''{command}'''",
                "",
            ]
        )
    lines.append(MANAGED_END)
    return "\n".join(lines) + "\n"


def should_refresh_codex_hook_trust(config: Path, explicit_config: Path | None) -> bool:
    default_config = Path.home() / ".codex" / "config.toml"
    try:
        return config.expanduser().resolve() == default_config.expanduser().resolve()
    except OSError:
        return explicit_config is None


def resolve_codex_hook_hashes(
    config_path: Path,
    cwd: Path | None = None,
    timeout_seconds: float = 8.0,
) -> dict[str, str]:
    codex = codex_cli_path()
    if codex is None:
        return {}

    try:
        process = subprocess.Popen(
            [str(codex), "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(cwd or Path.cwd()),
        )
    except OSError:
        return {}

    messages: queue.Queue[tuple[str, str]] = queue.Queue()

    def read_stream(name: str, stream: Any) -> None:
        for line in stream:
            messages.put((name, line.rstrip("\n")))

    for name, stream in (("out", process.stdout), ("err", process.stderr)):
        if stream is not None:
            threading.Thread(target=read_stream, args=(name, stream), daemon=True).start()

    def send(payload: dict[str, Any]) -> bool:
        if process.stdin is None:
            return False
        try:
            process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except OSError:
            return False
        return True

    def wait_for_id(message_id: int) -> dict[str, Any] | None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                name, line = messages.get(timeout=0.1)
            except queue.Empty:
                continue
            if name != "out":
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("id") == message_id:
                return payload
        return None

    try:
        if not send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "sidepulse", "version": "0"},
                    "capabilities": None,
                },
            }
        ):
            return {}
        wait_for_id(1)
        if not send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "hooks/list",
                "params": {"cwds": [str(cwd or Path.cwd())]},
            }
        ):
            return {}
        response = wait_for_id(2)
    finally:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()

    if not response:
        return {}

    try:
        hooks = response["result"]["data"][0]["hooks"]
    except (KeyError, IndexError, TypeError):
        return {}

    source_path = str(config_path.expanduser())
    trusted_hashes: dict[str, str] = {}
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        command = hook.get("command")
        current_hash = hook.get("currentHash")
        key = hook.get("key")
        if hook.get("sourcePath") != source_path:
            continue
        if not isinstance(command, str) or "hook_entry.py" not in command:
            continue
        if not isinstance(current_hash, str) or not isinstance(key, str):
            continue
        trusted_hashes[key] = current_hash
    return trusted_hashes


def codex_cli_path() -> Path | None:
    env_path = os.environ.get("CODEX_CLI_PATH")
    candidates = [
        Path(env_path).expanduser() if env_path else None,
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path("/Applications/Codex.app/Contents/Resources/codex"),
        Path(shutil.which("codex")).expanduser() if shutil.which("codex") else None,
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def update_codex_trusted_hashes(text: str, trusted_hashes: dict[str, str]) -> str:
    if not trusted_hashes:
        return text

    result = _ensure_hooks_state_table(text)
    for key, trusted_hash in trusted_hashes.items():
        result = _set_codex_trusted_hash(result, key, trusted_hash)
    return result


def _ensure_hooks_state_table(text: str) -> str:
    if re.search(r"^\s*\[hooks\.state\]\s*$", text, re.MULTILINE):
        return text
    return _ensure_trailing_newline(text) + "\n[hooks.state]\n"


def _set_codex_trusted_hash(text: str, key: str, trusted_hash: str) -> str:
    header = f'[hooks.state."{toml_basic_string_escape(key)}"]'
    lines = text.splitlines(keepends=True)
    header_index = None
    for index, line in enumerate(lines):
        if line.strip() == header:
            header_index = index
            break

    if header_index is None:
        block = f'\n{header}\ntrusted_hash = "{toml_basic_string_escape(trusted_hash)}"\n'
        return _ensure_trailing_newline(text) + block

    end = len(lines)
    for index in range(header_index + 1, len(lines)):
        if re.match(r"\s*\[.*\]\s*$", lines[index]):
            end = index
            break

    trusted_line = f'trusted_hash = "{toml_basic_string_escape(trusted_hash)}"\n'
    for index in range(header_index + 1, end):
        if re.match(r"\s*trusted_hash\s*=", lines[index]):
            lines[index] = trusted_line
            return "".join(lines)

    lines.insert(header_index + 1, trusted_line)
    return "".join(lines)


def toml_basic_string_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def strip_managed_block(text: str) -> str:
    # Codex may append its own tables between these comments when it rewrites
    # config.toml.  Remove only the comments; hook tables are removed below.
    return "\n".join(
        line for line in text.splitlines() if line.strip() not in {MANAGED_START, MANAGED_END}
    ) + ("\n" if text.endswith("\n") else "")


def remove_codex_hook_blocks_for_log(text: str, log_path: Path) -> str:
    target = str(log_path)
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    index = 0

    while index < len(lines):
        event_match = re.match(r"\s*\[\[hooks\.([A-Za-z0-9_]+)\]\]\s*$", lines[index])
        if event_match and event_match.group(1) in CODEX_EVENTS:
            event_name = event_match.group(1)
            end = index + 1
            nested = re.compile(rf"\s*\[\[hooks\.{re.escape(event_name)}\.hooks\]\]\s*$")
            table = re.compile(r"\s*\[.*\]\s*$")
            while end < len(lines):
                if table.match(lines[end]) and not nested.match(lines[end]):
                    break
                end += 1
            block = "".join(lines[index:end])
            if target in block or "sidepulse hook-log" in block or "hook_entry.py" in block:
                index = end
                continue

        if "Event logging hooks:" in lines[index] and target in text:
            index += 1
            continue

        out.append(lines[index])
        index += 1

    return "".join(out)


def is_pristine_codex_hook_install(text: str, block: str, log_path: Path) -> bool:
    if ensure_codex_hooks_feature(text) != text or text.count(block) != 1:
        return False
    unmanaged_text = text.replace(block, "", 1)
    return remove_codex_hook_blocks_for_log(unmanaged_text, log_path) == unmanaged_text


def remove_claude_hooks_for_log(entries: list[Any], log_path: Path) -> list[dict[str, Any]]:
    return remove_json_command_hooks_for_log(entries, log_path, "claude")


def remove_json_command_hooks_for_log(
    entries: list[Any],
    log_path: Path,
    provider: str,
) -> list[dict[str, Any]]:
    cleaned_entries: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            continue
        cleaned_hooks = []
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            command = hook.get("command")
            if is_sidepulse_json_hook_command(command, log_path, provider):
                continue
            cleaned_hooks.append(hook)
        if cleaned_hooks:
            kept = dict(entry)
            kept["hooks"] = cleaned_hooks
            cleaned_entries.append(kept)
    return cleaned_entries


def is_sidepulse_json_hook_command(
    command: Any,
    log_path: Path,
    provider: str,
) -> bool:
    if not isinstance(command, str):
        return False
    try:
        arguments = shlex.split(command)
    except ValueError:
        return False

    if _command_option(arguments, "--provider") != provider:
        return False
    if _command_option(arguments, "--log") != str(log_path.expanduser()):
        return False

    source_entrypoint = any(Path(argument).name == "hook_entry.py" for argument in arguments)
    packaged_entrypoint = (
        any(Path(argument).name == "agent-monitor" for argument in arguments)
        and "hook-log" in arguments
    )
    return source_entrypoint or packaged_entrypoint


def _command_option(arguments: list[str], option: str) -> str | None:
    for index, argument in enumerate(arguments):
        if argument == option and index + 1 < len(arguments):
            return arguments[index + 1]
        prefix = f"{option}="
        if argument.startswith(prefix):
            return argument.removeprefix(prefix)
    return None


def ensure_codex_hooks_feature(text: str) -> str:
    lines = text.splitlines(keepends=True)
    features_index = None
    for index, line in enumerate(lines):
        if re.match(r"\s*\[features\]\s*$", line):
            features_index = index
            break

    if features_index is None:
        return _ensure_trailing_newline(text) + "\n[features]\nhooks = true\n"

    end = len(lines)
    for index in range(features_index + 1, len(lines)):
        if re.match(r"\s*\[.*\]\s*$", lines[index]):
            end = index
            break

    for index in range(features_index + 1, end):
        if re.match(r"\s*hooks\s*=", lines[index]):
            lines[index] = "hooks = true\n"
            return "".join(lines)

    lines.insert(end, "hooks = true\n")
    return "".join(lines)


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    backup.write_bytes(path.read_bytes())
    return backup


def _ensure_trailing_newline(text: str) -> str:
    return text if not text or text.endswith("\n") else text + "\n"


def _normalize_config_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return stripped + "\n"
