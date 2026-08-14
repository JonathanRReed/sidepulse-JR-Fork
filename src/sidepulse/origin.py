from __future__ import annotations

import os
import re
import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import provider_label


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int | None
    comm: str
    command: str


@dataclass(frozen=True)
class AgentOrigin:
    kind: str
    label: str
    source: str
    confidence: str = "inferred"

    def to_payload(self) -> dict[str, str]:
        return {
            "agent_origin": self.label,
            "agent_origin_kind": self.kind,
            "agent_origin_source": self.source,
            "agent_origin_confidence": self.confidence,
        }


# A session's origin is decided by where it was launched from, which
# cannot change while that session lives -- verified in the field across
# 46 sessions / 1168 events, none of which ever produced a second
# origin. Deriving it per hook cost ~24% of every hook's wall time,
# because process_ancestry() forks /bin/ps once per ancestor.
_ORIGIN_CACHE_MAX_SESSIONS = 256
_origin_cache: dict[tuple[str, str], AgentOrigin] = {}


def clear_origin_cache(session_id: str | None = None) -> None:
    """Forget one session's origin, or all of them."""
    if session_id is None:
        _origin_cache.clear()
        return
    for key in [key for key in _origin_cache if key[1] == str(session_id)]:
        del _origin_cache[key]


def detect_agent_origin(
    provider: str,
    *,
    env: Mapping[str, str] | None = None,
    parent_pid: int | None = None,
    session_id: str | None = None,
) -> AgentOrigin:
    active_env = os.environ if env is None else env
    normalized_provider = provider.lower()

    cache_key = None
    if session_id:
        cache_key = (normalized_provider, str(session_id))
        cached = _origin_cache.get(cache_key)
        if cached is not None:
            return cached

    origin = _detect_agent_origin_uncached(
        normalized_provider, active_env, parent_pid
    )
    if cache_key is not None:
        if len(_origin_cache) >= _ORIGIN_CACHE_MAX_SESSIONS:
            # Bounded: a long-lived listener must not accumulate forever.
            _origin_cache.pop(next(iter(_origin_cache)), None)
        _origin_cache[cache_key] = origin
    return origin


def _detect_agent_origin_uncached(
    normalized_provider: str,
    active_env: Mapping[str, str],
    parent_pid: int | None,
) -> AgentOrigin:
    provider = normalized_provider
    explicit = explicit_origin_from_env(normalized_provider, active_env)
    if explicit is not None:
        return explicit

    env_origin = origin_from_environment(normalized_provider, active_env)
    if env_origin is not None:
        return env_origin

    processes = process_ancestry(parent_pid or os.getppid())
    process_origin = origin_from_processes(normalized_provider, processes)
    if process_origin is not None:
        return process_origin

    if terminal_environment(active_env):
        return surface_origin(normalized_provider, "cli", "env:TERM_PROGRAM")

    return AgentOrigin(
        kind=f"{normalized_provider}_unknown",
        label=provider_label(normalized_provider),
        source="fallback:provider",
        confidence="unknown",
    )


def explicit_origin_from_env(provider: str, env: Mapping[str, str]) -> AgentOrigin | None:
    label = clean_label(env.get("SIDEPULSE_AGENT_ORIGIN"))
    if not label:
        return None
    kind = clean_label(env.get("SIDEPULSE_AGENT_ORIGIN_KIND")) or normalize_kind(label)
    return AgentOrigin(
        kind=kind,
        label=label,
        source="env:SIDEPULSE_AGENT_ORIGIN",
        confidence="explicit",
    )


def origin_from_environment(provider: str, env: Mapping[str, str]) -> AgentOrigin | None:
    term_program = str(env.get("TERM_PROGRAM") or "").strip().lower()
    if term_program == "vscode" or any(key.startswith("VSCODE_") for key in env):
        return surface_origin(provider, "vscode", "env:VSCODE")

    bundle_id = str(env.get("__CFBundleIdentifier") or "").strip().lower()
    if bundle_id:
        if provider == "codex" and any(part in bundle_id for part in ("openai", "chatgpt", "codex")):
            return surface_origin(provider, "app", "env:__CFBundleIdentifier")
        if provider == "claude" and "anthropic" in bundle_id:
            return surface_origin(provider, "app", "env:__CFBundleIdentifier")
        if provider == "grok" and "grok" in bundle_id:
            return surface_origin(provider, "app", "env:__CFBundleIdentifier")

    return None


def origin_from_processes(
    provider: str,
    processes: tuple[ProcessInfo, ...],
) -> AgentOrigin | None:
    haystack = "\n".join(f"{info.comm}\n{info.command}" for info in processes).lower()

    if any(token in haystack for token in ("visual studio code.app", "code helper", "vscode")):
        return surface_origin(provider, "vscode", "process:Visual Studio Code")
    if "cursor.app" in haystack or "cursor helper" in haystack:
        return surface_origin(provider, "cursor", "process:Cursor")
    if "windsurf.app" in haystack or "windsurf helper" in haystack:
        return surface_origin(provider, "windsurf", "process:Windsurf")

    if provider == "codex" and any(
        token in haystack for token in ("codex.app", "chatgpt.app")
    ):
        return surface_origin(provider, "app", "process:Codex.app")
    if provider == "claude" and "claude.app" in haystack:
        return surface_origin(provider, "app", "process:Claude.app")
    if provider == "grok" and "grok.app" in haystack:
        return surface_origin(provider, "app", "process:Grok.app")

    for info in processes:
        basename = process_basename(info)
        if provider == "codex" and basename == "codex":
            return surface_origin(provider, "cli", "process:codex")
        if provider == "claude" and basename in {"claude", "claude-code"}:
            return surface_origin(provider, "cli", "process:claude")
        if provider == "devin" and basename == "devin":
            return surface_origin(provider, "cli", "process:devin")
        if provider == "grok" and basename == "grok":
            return surface_origin(provider, "cli", "process:grok")

    return None


def surface_origin(provider: str, surface: str, source: str) -> AgentOrigin:
    labels = {
        ("codex", "app"): "Codex UI",
        ("codex", "cli"): "Codex CLI",
        ("codex", "vscode"): "Codex in VS Code",
        ("codex", "cursor"): "Codex in Cursor",
        ("codex", "windsurf"): "Codex in Windsurf",
        ("codex", "transcript"): "Codex Transcript",
        ("claude", "app"): "Claude App",
        ("claude", "cli"): "Claude Code CLI",
        ("claude", "vscode"): "Claude in VS Code",
        ("claude", "cursor"): "Claude in Cursor",
        ("claude", "windsurf"): "Claude in Windsurf",
        ("claude", "transcript"): "Claude Transcript",
        ("devin", "cli"): "Devin CLI",
        ("devin", "vscode"): "Devin in VS Code",
        ("devin", "cursor"): "Devin in Cursor",
        ("devin", "windsurf"): "Devin in Windsurf",
        ("grok", "app"): "Grok App",
        ("grok", "cli"): "Grok CLI",
        ("grok", "vscode"): "Grok in VS Code",
        ("grok", "cursor"): "Grok in Cursor",
        ("grok", "windsurf"): "Grok in Windsurf",
        ("grok", "transcript"): "Grok Transcript",
    }
    label = labels.get((provider, surface), provider_label(provider))
    return AgentOrigin(
        kind=f"{provider}_{surface}",
        label=label,
        source=source,
    )


def process_ancestry(pid: int, *, limit: int = 10) -> tuple[ProcessInfo, ...]:
    seen: set[int] = set()
    result: list[ProcessInfo] = []
    current = pid
    while current > 1 and current not in seen and len(result) < limit:
        seen.add(current)
        info = process_info(current)
        if info is None:
            break
        result.append(info)
        if info.ppid is None:
            break
        current = info.ppid
    return tuple(result)


def process_info(pid: int) -> ProcessInfo | None:
    try:
        completed = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "pid=", "-o", "ppid=", "-o", "comm=", "-o", "command="],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=0.4,
        )
    except Exception:
        return None
    line = completed.stdout.strip()
    if completed.returncode != 0 or not line:
        return None

    parts = line.split(None, 3)
    if len(parts) < 3:
        return None
    try:
        parsed_pid = int(parts[0])
        parsed_ppid = int(parts[1])
    except ValueError:
        return None
    comm = parts[2]
    command = parts[3] if len(parts) > 3 else comm
    return ProcessInfo(parsed_pid, parsed_ppid, comm, command)


def process_basename(info: ProcessInfo) -> str:
    candidates = [info.comm]
    try:
        split = shlex.split(info.command)
    except ValueError:
        split = []
    if split:
        candidates.append(split[0])

    for value in candidates:
        name = Path(value).name.strip().lower()
        if name and name not in {"sh", "bash", "zsh", "python", "python3", "env"}:
            return name
    return ""


def terminal_environment(env: Mapping[str, str]) -> bool:
    term_program = str(env.get("TERM_PROGRAM") or "").strip()
    return bool(term_program and term_program.lower() != "vscode")


def annotate_payload_with_origin(
    provider: str,
    payload: dict[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if "agent_origin" in payload or "agentOrigin" in payload:
        return payload
    result = dict(payload)
    # Key the cache on this hook's own session: origin cannot change
    # within a session, and re-deriving it forks /bin/ps per ancestor.
    session_id = payload.get("session_id") or payload.get("sessionId")
    if payload.get("hook_event_name") == "SessionStart" and session_id:
        clear_origin_cache(str(session_id))
    result.update(
        detect_agent_origin(
            provider,
            env=env,
            session_id=str(session_id) if session_id else None,
        ).to_payload()
    )
    return result


def origin_label_from_payload(provider: str, raw: Mapping[str, Any]) -> str | None:
    for key in ("agent_origin", "agentOrigin", "agent_origin_label", "origin_label"):
        label = clean_label(raw.get(key))
        if label:
            return label

    structured = raw.get("sidepulse_origin") or raw.get("sidepulseOrigin")
    if isinstance(structured, Mapping):
        for key in ("label", "name", "origin"):
            label = clean_label(structured.get(key))
            if label:
                return label
    elif isinstance(structured, str):
        label = clean_label(structured)
        if label:
            return label

    source = clean_label(raw.get("source"))
    if source in {"codex-transcripts", "claude-transcripts"}:
        return surface_origin(provider, "transcript", f"source:{source}").label

    return None


def clean_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text or None


def normalize_kind(label: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return normalized or "custom"
