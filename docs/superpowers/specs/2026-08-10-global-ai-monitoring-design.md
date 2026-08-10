# Global AI Monitoring Design

Date: 2026-08-10

## Objective

Run SidePulse globally on Jonathan's Mac and report useful lifecycle states for every locally supported AI agent. The initial supported set is Codex, Claude Code, Devin CLI, and the existing Grok integration. The design must preserve unrelated hooks and make future providers straightforward to add.

## Scope

This change will:

- Add first-class Devin CLI monitoring.
- Replace scattered provider lists and dispatch conditionals with a central provider registry.
- Install global hooks for Codex, Claude Code, Devin CLI, and Grok.
- Preserve existing hooks, including Agent Deck hooks in Devin's configuration.
- Show Devin as a distinct provider and origin in status output and the menu-bar app.
- Install and launch the existing SidePulse helpers, then connect them to the mounted device.
- Document the provider adapter contract.

This change will not:

- Monitor arbitrary AI applications that expose no hook, event, log, or process interface.
- Send agent prompts, transcripts, credentials, or hook payloads to a remote service.
- Add production dependencies.
- Commit, push, publish, or deploy changes.

## Architecture

### Provider registry

`providers.py` will define an immutable provider specification for each supported agent. Each specification owns:

- Provider identifier and display label.
- Supported lifecycle events.
- Global configuration path.
- Configuration detector.
- Hook installer and uninstaller routing metadata.
- Default log path.

The registry will be the source of truth for CLI choices, default log sources, doctor output, setup loops, and status-bar provider enumeration. Provider-specific behavior that cannot be expressed as data will remain in focused adapter functions.

### Devin adapter

Devin CLI will use `~/.config/devin/config.json`. Its adapter will merge SidePulse command hooks into the existing top-level `hooks` object for these events:

- `SessionStart`
- `UserPromptSubmit`
- `PreToolUse`
- `PostToolUse`
- `PermissionRequest`
- `PostCompaction`
- `Stop`
- `SessionEnd`

The installer will remove only prior SidePulse hooks targeting the same provider log, append one current SidePulse hook per event, preserve all unrelated entries, and write a timestamped backup before changing an existing file. Repeated installation will be idempotent.

Devin events will be written to `~/.local/state/sidepulse/agent-monitor/devin.jsonl`. They will use `provider: devin` before origin detection so Devin cannot be misreported as Claude even though Devin can import Claude hook configuration.

### Event normalization

The existing normalized event model will remain unchanged. Provider aliases will translate Devin's `PostCompaction` event to SidePulse's canonical `PostCompact` event. Devin's `prompt_id` will be accepted as a turn identifier when `turn_id` is absent. Existing session, tool, permission, stop, and completion mappings will apply to Devin payloads.

### Presentation

Provider labels and origin detection will include:

- `Devin`
- `Devin CLI`
- `Devin in VS Code`, Cursor, or Windsurf when the existing surface detection identifies those hosts

The status-bar provider badge will use the existing generic badge fallback unless a native Devin asset already exists. No new visual asset is required for this change.

## Data flow

1. An agent emits a lifecycle hook with JSON on standard input.
2. The global provider hook invokes SidePulse's standard-library-only hook entry point with an explicit provider and log path.
3. The hook entry point annotates origin, sends a best-effort local Unix socket event to the menu-bar process, appends provider JSONL, and records the compact status audit.
4. The collector normalizes the event and updates the latest state for that provider session.
5. The aggregator selects the most actionable fresh state across all agents.
6. The status-bar helper writes the matching LED program to the mounted SidePulse device.

All runtime communication stays local.

## Installation and rollback

The global setup sequence will:

1. Install the fork into the per-user virtual environment at `~/.local/share/sidepulse/venv` so hooks and LaunchAgents use a stable executable without changing the system Python.
2. Run a dry-run for all provider configuration changes.
3. Install Codex, Claude, Devin, and Grok hooks globally.
4. Install and start the per-user status-bar and eject-guard LaunchAgents.
5. Verify the mounted SidePulse target and perform a bounded LED write.

Every modified existing agent config receives a timestamped sibling backup. The provider uninstall commands remove only managed SidePulse hooks. Existing Agent Deck and user hooks remain untouched.

## Error handling

- Missing provider config files are created only during explicit setup.
- Invalid JSON fails before writing and leaves the original file unchanged.
- Hook delivery and logging remain best effort and never block the agent on SidePulse failure.
- Missing hardware does not stop event collection or the menu-bar app.
- Duplicate setup runs converge to one managed SidePulse hook per provider event.
- LaunchAgent and LED verification failures are reported separately from source-test results.

## Testing and verification

Automated coverage will include:

- Provider registry enumeration.
- Devin config detection.
- Devin installation, idempotency, backup creation, and preservation of unrelated hooks.
- Devin uninstallation that removes only SidePulse hooks.
- `PostCompaction` and `prompt_id` normalization.
- Devin origin labeling.
- CLI parsing and setup routing for Devin.
- Default source and status-bar enumeration.

Completion requires fresh evidence from:

- Python lint or the closest repository-supported static check.
- Python compilation.
- The complete unit test suite.
- Dry-run doctor and install output.
- Global config inspection showing all providers installed and existing hooks preserved.
- A synthetic lifecycle sequence for Codex, Claude, and Devin that produces distinct provider statuses.
- A running menu-bar LaunchAgent.
- A verified write to `/Volumes/SidePulse/LEDS.LED`.

## Future providers

A future provider must supply a registry entry, its event set, a config detector, and an installer adapter when its configuration format differs from an existing adapter. Generic hook payloads then flow through the shared logger, parser, collector, aggregator, and LED renderer without adding provider branches to those layers.

Process-only detection may be added later as a low-confidence fallback for agents without lifecycle hooks. It is intentionally outside this implementation because it cannot reliably distinguish working, waiting, failed, and completed states.
