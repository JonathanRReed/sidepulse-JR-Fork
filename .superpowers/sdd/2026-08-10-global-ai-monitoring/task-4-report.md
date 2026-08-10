# Task 4 Report: Documentation and Configuration Dry Run

## Implementation

- Added Devin to the supported-provider table with its global configuration path and default JSONL log path.
- Updated setup, install, uninstall, marker-guidance, and status-bar Settings wording for Codex, Claude, Devin, and Grok.
- Documented preservation of unrelated hooks, timestamped adjacent backups, targeted rollback with `sidepulse agent-monitor uninstall <provider>`, and full-file restoration from a backup.
- Documented the future-provider contract: registry entry, supported event set, configuration detector, and preserving installer/uninstaller adapter.
- Updated the hook-entry module docstring so its supported-provider wording matches the CLI registry.

## Dry-Run and Preservation Evidence

- `doctor --json` enumerated four providers in registry order: Codex, Claude, Devin, and Grok.
- `install --dry-run` reported bounded would-update actions for all four providers. It made no writes.
- A temporary Devin configuration with two unrelated Agent Deck hook entries for every Devin event remained byte-identical after `dry_run=True`.
- A real install in that temporary home retained both unrelated entries and exactly one SidePulse entry per event. A second install made no change.
- No real global configuration file was modified and no configuration contents were printed.

## Verification

```text
rg -n "Codex, Claude|Codex, Claude, and/or Grok|codex.*claude.*grok" README.md src
Result: exit 0 with no matches.

/opt/homebrew/bin/ruff check src tests
Result: exit 0, All checks passed.

PYTHONPATH=src /opt/homebrew/bin/python3.13 -m unittest \
  tests.test_sidepulse.AgentMonitorTests.test_devin_installer_preserves_agent_deck_hooks_and_is_idempotent \
  tests.test_sidepulse.AgentMonitorTests.test_devin_uninstaller_removes_only_sidepulse_hooks \
  tests.test_sidepulse.AgentMonitorTests.test_devin_cli_install_and_log_arguments_are_available -v
Result: exit 0, 3 tests passed.

PYTHONPATH=src /opt/homebrew/bin/python3.13 -m unittest discover -s tests -v
Result: exit 0, 175 tests passed, 29 platform/dependency skips.

PYTHONPATH=src /opt/homebrew/bin/python3.13 -m compileall -q src tests
Result: exit 0.

git diff --check
Result: exit 0.
```

## Files

- `README.md`
- `src/sidepulse/hook_entry.py`
- `.superpowers/sdd/2026-08-10-global-ai-monitoring/task-4-report.md`

## Remaining Risk

The temporary preservation fixture exercises the Devin adapter without exposing real configuration content. The real global dry runs verify registry enumeration and no-write behavior, but intentionally do not prove a live global install or rollback.
