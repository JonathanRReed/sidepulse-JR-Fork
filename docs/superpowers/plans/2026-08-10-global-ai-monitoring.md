# Global AI Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add provider-registry-driven global monitoring for Codex, Claude Code, Devin CLI, and Grok, then install and verify SidePulse on this Mac.

**Architecture:** A central immutable provider registry becomes the source of truth for provider identity, event sets, configuration detection, log sources, CLI enumeration, and status-bar enumeration. A dedicated Devin JSON adapter safely merges SidePulse hooks into `~/.config/devin/config.json`, while the shared hook logger, collector, aggregator, and LED renderer continue to handle normalized events.

**Tech Stack:** Python 3.13, standard-library `dataclasses`, JSON and TOML configuration, `unittest`, Ruff, PyObjC, macOS LaunchAgents, SidePulse `LEDS.LED` device protocol.

## Global Constraints

- Install globally for the current macOS user.
- Preserve all unrelated agent configuration and existing Agent Deck hooks.
- Create timestamped backups before changing existing agent configuration files.
- Do not send prompts, transcripts, credentials, or hook payloads to remote services.
- Add no production dependencies.
- Keep hook delivery best effort so SidePulse failure never blocks an agent.
- Do not commit, push, publish, deploy, or open a pull request unless the user asks.
- Use `/opt/homebrew/bin/python3.13` for the runtime installation.
- Install the runtime under `~/.local/share/sidepulse/venv`.

---

## File Structure

- `src/sidepulse/providers.py`: provider specifications, config detection, log resolution, and payload normalization.
- `src/sidepulse/install.py`: provider-specific install and uninstall adapters plus registry-based routing.
- `src/sidepulse/cli.py`: provider-derived command choices, log arguments, setup routing, and doctor output.
- `src/sidepulse/audit.py`: remove the existing unused `Iterable` import so repository lint can pass.
- `src/sidepulse/collector.py`: provider-derived default log sources.
- `src/sidepulse/models.py`: provider display labels.
- `src/sidepulse/origin.py`: Devin process and host-surface origin labels.
- `src/sidepulse/status_bar.py`: provider-derived replay, settings, install controls, and generic Devin badge behavior.
- `tests/test_sidepulse.py`: provider registry, Devin adapter, normalization, routing, preservation, and presentation tests.
- `README.md`: global setup instructions, supported providers, Devin log path, and future adapter contract.

### Task 1: Provider Registry and Devin Event Normalization

**Files:**
- Modify: `src/sidepulse/providers.py:19-374`
- Modify: `src/sidepulse/models.py:156-161`
- Test: `tests/test_sidepulse.py`

**Interfaces:**
- Produces: `ProviderSpec`, `PROVIDER_SPECS`, `PROVIDER_REGISTRY`, `HOOK_PROVIDERS`, `provider_spec(provider: str) -> ProviderSpec`, and `detect_devin_config(home: Path | None = None) -> ProviderConfig`.
- Produces: canonical `PostCompact` events and `HookEvent.turn_id` populated from Devin `prompt_id`.
- Consumes: existing `ProviderConfig`, `default_log_path`, JSON hook entry parsing, and `provider_label`.

- [ ] **Step 1: Add failing registry, detection, and normalization tests**

Add imports for `DEVIN_EVENTS`, `HOOK_PROVIDERS`, `PROVIDER_REGISTRY`, `detect_devin_config`, and `provider_spec`. Add tests with these assertions:

```python
def test_provider_registry_includes_devin_as_first_class_provider(self) -> None:
    self.assertEqual(HOOK_PROVIDERS, ("codex", "claude", "devin", "grok"))
    self.assertEqual(provider_spec("devin").label, "Devin")
    self.assertEqual(provider_spec("devin").config_kind, "devin-json")
    self.assertIs(PROVIDER_REGISTRY["devin"], provider_spec("devin"))

def test_detect_devin_config_reads_global_hooks(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        config = home / ".config" / "devin" / "config.json"
        log = home / "state" / "devin.jsonl"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "hooks": [{
                        "type": "command",
                        "command": f"python hook_entry.py --provider devin --log {log}",
                    }],
                }],
            },
        }))

        detected = detect_devin_config(home)

        self.assertEqual(detected.provider, "devin")
        self.assertTrue(detected.hooks_enabled)
        self.assertIn("PreToolUse", detected.hook_events)
        self.assertIn(log, detected.log_paths)

def test_devin_post_compaction_and_prompt_id_are_normalized(self) -> None:
    record = parse_log_line("devin", json.dumps({
        "hook_event_name": "PostCompaction",
        "session_id": "devin-session",
        "prompt_id": "devin-turn",
        "summary": "compacted",
    }))

    self.assertIsNotNone(record)
    self.assertEqual(record.provider, "devin")
    self.assertEqual(record.event_name, "PostCompact")
    self.assertEqual(record.turn_id, "devin-turn")
```

- [ ] **Step 2: Run the focused tests and confirm the red state**

Run:

```bash
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m unittest \
  tests.test_sidepulse.AgentMonitorTests.test_provider_registry_includes_devin_as_first_class_provider \
  tests.test_sidepulse.AgentMonitorTests.test_detect_devin_config_reads_global_hooks \
  tests.test_sidepulse.AgentMonitorTests.test_devin_post_compaction_and_prompt_id_are_normalized -v
```

Expected: import errors or assertion failures because Devin registry symbols and aliases do not exist.

- [ ] **Step 3: Add the provider specification and registry**

Define the provider event set and specification:

```python
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

@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    label: str
    events: tuple[str, ...]
    config_kind: str
    config_path: Callable[[Path | None], Path]
    detector: Callable[[Path | None], ProviderConfig]
```

After defining all config-path and detector functions, build the registry:

```python
PROVIDER_SPECS = (
    ProviderSpec("codex", "Codex", CODEX_EVENTS, "codex-toml", default_codex_config_path, detect_codex_config),
    ProviderSpec("claude", "Claude", CLAUDE_EVENTS, "claude-json", default_claude_config_path, detect_claude_config),
    ProviderSpec("devin", "Devin", DEVIN_EVENTS, "devin-json", default_devin_config_path, detect_devin_config),
    ProviderSpec("grok", "Grok", GROK_EVENTS, "grok-json", default_grok_hook_config_path, detect_grok_config),
)
PROVIDER_REGISTRY = {spec.provider: spec for spec in PROVIDER_SPECS}
HOOK_PROVIDERS = tuple(PROVIDER_REGISTRY)

def provider_spec(provider: str) -> ProviderSpec:
    try:
        return PROVIDER_REGISTRY[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported hook provider: {provider}") from exc
```

Import `Callable` from `collections.abc`. Extract `default_codex_config_path` and `default_claude_config_path` from the existing inline paths. Move `detect_provider_configs` below the registry and implement it as `[spec.detector(home) for spec in PROVIDER_SPECS]`. Build `KNOWN_EVENTS` from every specification, but include the canonical `PostCompact` alias so collector behavior remains stable.

- [ ] **Step 4: Implement Devin config detection through the registry**

Add:

```python
def default_devin_config_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / ".config" / "devin" / "config.json"

def detect_devin_config(home: Path | None = None) -> ProviderConfig:
    config_path = default_devin_config_path(home)
    return detect_json_hook_config("devin", config_path, DEVIN_EVENTS)
```

Extract a focused `detect_json_hook_config(provider, config_path, allowed_events)` helper from the common Claude, Devin, and Grok JSON scanning behavior. Keep Grok's canonical-event normalization when reading its compatibility event names. Implement `detect_log_path` with `provider_spec(provider).detector(home)` and retain the current default-log fallback for unknown provider identifiers.

- [ ] **Step 5: Normalize Devin event aliases**

Add `"post_compaction": "PostCompact"` to `canonical_event_name`, copy `prompt_id` into `turn_id`, and add `"devin": "Devin"` to `provider_label`.

- [ ] **Step 6: Run focused and complete provider tests**

Run the three focused tests from Step 2, then:

```bash
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m unittest tests.test_sidepulse.AgentMonitorTests -v
```

Expected: focused tests pass and existing provider tests remain green.

### Task 2: Safe Global Devin Hook Adapter

**Files:**
- Modify: `src/sidepulse/install.py:18-570`
- Test: `tests/test_sidepulse.py:1239-1545`

**Interfaces:**
- Consumes: `DEVIN_EVENTS`, `default_devin_config_path`, `detect_log_path`, `hook_command`, `backup_file`, and `remove_json_command_hooks_for_log`.
- Produces: `install_devin_hooks(...) -> InstallResult`, `uninstall_devin_hooks(...) -> InstallResult`, `install_provider_hooks(...) -> InstallResult`, and `uninstall_provider_hooks(...) -> InstallResult`.

- [ ] **Step 1: Add failing preservation, idempotency, and uninstall tests**

Add:

```python
def test_devin_installer_preserves_agent_deck_hooks_and_is_idempotent(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = base / "config.json"
        log = base / "devin.jsonl"
        agent_deck = "/opt/homebrew/bin/bun /tmp/agent-deck-hook.ts"
        config.write_text(json.dumps({
            "theme_mode": "dark",
            "hooks": {
                "PreToolUse": [{
                    "matcher": "^exec$",
                    "hooks": [{"type": "command", "command": agent_deck}],
                }],
            },
        }))

        first = install_devin_hooks(log, config, python_executable="python3")
        first_text = config.read_text()
        second = install_devin_hooks(log, config, python_executable="python3")

        data = json.loads(config.read_text())
        commands = [
            hook["command"]
            for entry in data["hooks"]["PreToolUse"]
            for hook in entry["hooks"]
        ]
        self.assertTrue(first.changed)
        self.assertIsNotNone(first.backup_path)
        self.assertFalse(second.changed)
        self.assertEqual(config.read_text(), first_text)
        self.assertEqual(commands.count(agent_deck), 1)
        self.assertEqual(sum("--provider devin" in command for command in commands), 1)
        self.assertEqual(data["theme_mode"], "dark")

def test_devin_uninstaller_removes_only_sidepulse_hooks(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = base / "config.json"
        log = base / "devin.jsonl"
        config.write_text(json.dumps({
            "hooks": {"Stop": [{"hooks": [{
                "type": "command",
                "command": "echo keep-agent-deck",
            }]}]},
        }))
        install_devin_hooks(log, config, python_executable="python3")

        result = uninstall_devin_hooks(log, config)

        self.assertTrue(result.changed)
        self.assertIn("keep-agent-deck", config.read_text())
        self.assertNotIn("--provider devin", config.read_text())
```

- [ ] **Step 2: Run the new tests and confirm the red state**

Run both exact unittest methods. Expected: import errors for the missing Devin functions.

- [ ] **Step 3: Implement the Devin JSON adapter**

Use the existing JSON cleaning helper and omit matchers for lifecycle events:

```python
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
        cleaned = remove_json_command_hooks_for_log(entries, target_log)
        cleaned.append({"hooks": [{"type": "command", "command": command}]})
        hooks[event_name] = cleaned
    return write_json_install_result("devin", config, target_log, data, original, dry_run)
```

Extract `write_json_install_result` only if it reduces duplicated Claude, Devin, and Grok write and backup logic without changing their behavior.

- [ ] **Step 4: Implement Devin uninstall and registry routing**

Implement `uninstall_devin_hooks` with the same targeted cleanup rule. Add adapter maps:

```python
INSTALLERS = {
    "codex": install_codex_hooks,
    "claude": install_claude_hooks,
    "devin": install_devin_hooks,
    "grok": install_grok_hooks,
}
UNINSTALLERS = {
    "codex": uninstall_codex_hooks,
    "claude": uninstall_claude_hooks,
    "devin": uninstall_devin_hooks,
    "grok": uninstall_grok_hooks,
}

def install_provider_hooks(provider: str, **kwargs: Any) -> InstallResult:
    return INSTALLERS[provider](**kwargs)

def uninstall_provider_hooks(provider: str, **kwargs: Any) -> InstallResult:
    return UNINSTALLERS[provider](**kwargs)
```

- [ ] **Step 5: Run adapter tests and the full unit suite**

Run the focused tests, then:

```bash
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m unittest discover -s tests -v
```

Expected: all non-platform-skipped tests pass.

### Task 3: Registry-Driven CLI, Collector, Origin, and Status Bar

**Files:**
- Modify: `src/sidepulse/audit.py:8`
- Modify: `src/sidepulse/cli.py:18-111, 520-886`
- Modify: `src/sidepulse/collector.py:477-488`
- Modify: `src/sidepulse/origin.py:71-154`
- Modify: `src/sidepulse/status_bar.py:210-230, 824-895, 2249-2270, 2629-2651`
- Test: `tests/test_sidepulse.py`

**Interfaces:**
- Consumes: `HOOK_PROVIDERS`, `provider_spec`, `install_provider_hooks`, `uninstall_provider_hooks`, and `detect_log_path`.
- Produces: CLI support for `--devin-log`, source and replay enumeration for Devin, and distinct `Devin CLI` origin labels.

- [ ] **Step 1: Add failing routing and presentation tests**

Add tests that assert:

```python
def test_devin_cli_install_and_log_arguments_are_available(self) -> None:
    parser = build_parser(prog="sidepulse agent-monitor")
    install = parser.parse_args(["install", "devin", "--devin-log", "/tmp/devin.jsonl"])
    hook_log = parser.parse_args([
        "hook-log", "--provider", "devin", "--log", "/tmp/devin.jsonl",
    ])
    self.assertEqual(install.provider, "devin")
    self.assertEqual(install.devin_log, Path("/tmp/devin.jsonl"))
    self.assertEqual(hook_log.provider, "devin")

def test_default_sources_include_registered_hook_providers(self) -> None:
    with patch("sidepulse.collector.load_settings", return_value=AgentMonitorSettings()):
        sources = default_sources()
    providers = tuple(source.provider for source in sources if not source.provider.endswith("-transcript"))
    self.assertEqual(providers, HOOK_PROVIDERS)

def test_origin_process_detection_identifies_devin_cli(self) -> None:
    origin = origin_from_processes(
        "devin",
        (ProcessInfo(pid=100, ppid=1, comm="/Users/me/.local/bin/devin", command="devin"),),
    )
    self.assertIsNotNone(origin)
    self.assertEqual(origin.label, "Devin CLI")
```

Update the setup routing test to include a mocked Devin install result and assert one call.

- [ ] **Step 2: Run focused tests and confirm the red state**

Expected: parser rejection for `devin`, missing default source, and generic origin label.

- [ ] **Step 3: Derive CLI arguments and routing from the registry**

Add provider log options with one loop in both parsers:

```python
for provider in HOOK_PROVIDERS:
    parser.add_argument(
        f"--{provider}-log",
        type=Path,
        help=f"{provider_spec(provider).label} JSONL log path.",
    )
```

Replace install and uninstall conditionals with `install_provider_hooks(provider, ...)` and `uninstall_provider_hooks(provider, ...)`. Keep `selected_hook_providers` and explicit provider ordering unchanged.

- [ ] **Step 4: Derive collector and status-bar enumeration from the registry**

Build hook sources from `HOOK_PROVIDERS`, inserting optional Codex and Claude transcript sources immediately after their hook source. Change status-bar replay and provider settings loops to `HOOK_PROVIDERS`. Route status-bar install controls through the provider install functions.

- [ ] **Step 5: Add Devin origin and badge behavior**

Extend process detection with the `devin` executable and add surface labels:

```python
("devin", "cli"): "Devin CLI",
("devin", "vscode"): "Devin in VS Code",
("devin", "cursor"): "Devin in Cursor",
("devin", "windsurf"): "Devin in Windsurf",
```

Use the existing generic terminal-symbol badge for Devin. Do not add an image asset. Devin sessions open in Terminal through the existing fallback.

- [ ] **Step 6: Remove the three baseline unused imports**

Remove `Iterable` from `src/sidepulse/audit.py`, remove the unused `detect_log_path` import from `src/sidepulse/cli.py`, and remove the unused `provider_label` import from `src/sidepulse/status_bar.py`. These are the three Ruff failures present before implementation.

- [ ] **Step 7: Run focused tests, full tests, lint, and compilation**

Run:

```bash
/opt/homebrew/bin/ruff check src tests
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m compileall -q src tests
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m unittest discover -s tests -v
```

Expected: Ruff and compilation exit zero; all non-platform-skipped tests pass. A full mypy run is not a completion gate because the unchanged baseline contains PyObjC stub gaps and existing unrelated typing errors.

### Task 4: Documentation and Configuration Dry Run

**Files:**
- Modify: `README.md:65-210`
- Test: command-level dry runs against temporary homes and the real configs without writes.

**Interfaces:**
- Consumes: the final CLI provider registry and Devin adapter.
- Produces: accurate setup, doctor, log-path, rollback, and future-provider instructions.

- [ ] **Step 1: Update supported-provider documentation**

Add Devin to the supported-provider table:

```markdown
| Devin | `~/.config/devin/config.json` | `${XDG_STATE_HOME:-~/.local/state}/sidepulse/agent-monitor/devin.jsonl` |
```

Update setup examples to include `sidepulse setup devin`, state that existing hook entries are preserved, and document the future provider contract as registry entry, event set, detector, and installer adapter.

- [ ] **Step 2: Run documentation and CLI consistency checks**

Run:

```bash
rg -n "Codex, Claude|Codex, Claude, and/or Grok|codex.*claude.*grok" README.md src
git diff --check
```

Expected: no stale user-facing three-provider lists and no whitespace errors.

- [ ] **Step 3: Dry-run the actual global configuration changes**

From the repository with `PYTHONPATH=src`, run:

```bash
PYTHONPATH=src /opt/homebrew/bin/python3.13 -c 'from sidepulse.cli import main; raise SystemExit(main(["doctor", "--json"]))'
PYTHONPATH=src /opt/homebrew/bin/python3.13 -c 'from sidepulse.cli import main; raise SystemExit(main(["install", "--dry-run"]))'
```

Expected: doctor enumerates four providers; install reports bounded changes for Codex, Claude, Devin, and Grok without modifying files.

- [ ] **Step 4: Verify dry-run preservation against a temporary Devin config**

Use a temporary home fixture containing two Agent Deck hook entries per Devin event, run the installer with `dry_run=True`, and verify the file hash is unchanged. Then run a real install inside that temporary home and verify both Agent Deck entries plus exactly one SidePulse entry remain for every Devin event.

### Task 5: Global Runtime Installation and Physical Verification

**Files:**
- Modify outside repository during authorized setup: `~/.codex/config.toml`, `~/.claude/settings.json`, `~/.config/devin/config.json`, and `~/.grok/hooks/sidepulse.json`.
- Create outside repository: `~/.local/share/sidepulse/venv`, SidePulse state logs, and per-user LaunchAgent files produced by existing setup code.
- Verify: `/Volumes/SidePulse/LEDS.LED`.

**Interfaces:**
- Consumes: completed source implementation and existing `sidepulse setup` flow.
- Produces: a running global SidePulse monitor and verified provider-specific event logs.

- [ ] **Step 1: Capture pre-install evidence**

Record SHA-256 hashes and hook-command counts for the three existing agent config files without recording credential values. Record existing Agent Deck command counts in Devin config and confirm `/Volumes/SidePulse/LEDS.LED` exists.

- [ ] **Step 2: Build the stable per-user virtual environment**

Run:

```bash
/opt/homebrew/bin/python3.13 -m venv /Users/jonathanreed/.local/share/sidepulse/venv
/Users/jonathanreed/.local/share/sidepulse/venv/bin/python -m pip install --upgrade pip
/Users/jonathanreed/.local/share/sidepulse/venv/bin/python -m pip install .
```

Expected: `sidepulse` and declared PyObjC dependencies install without changing the system Python.

- [ ] **Step 3: Run global setup**

Run:

```bash
/Users/jonathanreed/.local/share/sidepulse/venv/bin/sidepulse setup \
  --sd-eject-guard-scope user
```

Expected: each provider reports updated or already configured, timestamped backups are reported for changed existing configs, and both LaunchAgents report installed and started.

- [ ] **Step 4: Verify global config preservation and idempotency**

Check:

- Codex hooks are enabled and include one SidePulse command per supported event.
- Claude retains all unrelated hooks and includes one SidePulse command per supported event.
- Devin retains the exact pre-install Agent Deck command count and includes one `--provider devin` command per supported event.
- A second `sidepulse agent-monitor install` reports every provider already configured and creates no new backup.

- [ ] **Step 5: Inject provider-distinct synthetic lifecycle events**

For Codex, Claude, and Devin, pipe a `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, and `Stop` JSON payload into the installed `sidepulse agent-monitor hook-log` command with a unique session id and the provider's real log path. Verify `sidepulse agent-monitor status --json` reports three distinct providers and does not label Devin as Claude.

- [ ] **Step 6: Verify LaunchAgents and menu-bar process**

Run `launchctl print` for the generated SidePulse status-bar and eject-guard labels, confirm both processes are running, and inspect their current log tails for startup errors. This proves runtime state rather than only plist presence.

- [ ] **Step 7: Verify physical LED output**

Capture the current `LEDS.LED` content, run:

```bash
/Users/jonathanreed/.local/share/sidepulse/venv/bin/sidepulse write \
  "off\n#00ff66 1s solid" --device /Volumes/SidePulse
```

Verify the file content and modification time changed as expected. Then trigger a synthetic working event and verify the status-bar helper rewrites the device with the working pattern.

- [ ] **Step 8: Run final verification from the exact installed source state**

Run:

```bash
/opt/homebrew/bin/ruff check src tests
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m compileall -q src tests
PYTHONPATH=src /opt/homebrew/bin/python3.13 -m unittest discover -s tests -v
/Users/jonathanreed/.local/share/sidepulse/venv/bin/sidepulse agent-monitor doctor
git diff --check
git status --short
```

Expected: static checks and tests pass, doctor reports four configured providers, and the worktree contains only intentional source, test, documentation, design, and plan changes.

- [ ] **Step 9: Record rollback locations and remaining limitations**

Report every created backup path, the virtual environment path, the LaunchAgent paths, the provider log directory, and the targeted uninstall command. State that agents without hook or log interfaces remain unsupported until an adapter or low-confidence process fallback is added.
