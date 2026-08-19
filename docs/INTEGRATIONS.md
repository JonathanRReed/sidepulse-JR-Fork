# T3 Code integration

SidePulse consumes read-only status from T3 Code and displays provider/account usage and history computed by SidePulse providers. The integration is disabled by default and configured outside the primary `agent-monitor` settings document.

## Commands

Show configuration and the packaged compatibility window:

```bash
sidepulse integrations status
sidepulse integrations status --json
```

Enable or disable the integration:

```bash
sidepulse integrations enable t3code
sidepulse integrations disable t3code
```

Run a bounded compatibility probe:

```bash
sidepulse integrations probe t3code
sidepulse integrations probe t3code --json
```

Restart the SidePulse status-bar app after changing integration settings:

```bash
sidepulse status-bar stop
sidepulse status-bar start
```

The separate `sidepulse-integrations` console script exposes the same subcommands. The signed macOS application routes `sidepulse integrations ...` through the same implementation.

## T3 Code

### Supported data

The current adapter reads T3 Code’s local SQLite projection and preserves:

- project and thread IDs
- project and thread titles
- underlying provider and provider-instance identity
- provider thread ID when available
- model and reasoning-effort selection
- runtime and interaction mode
- branch and worktree path
- active/latest turn identity
- session status and failure presence
- pending approval, user-input, and actionable-plan indicators
- the official `t3code://threads/<environment>/<thread>` deep link when an environment ID is configured

SidePulse maps the projected lifecycle into its canonical agent states. A T3 approval, input request, or actionable plan becomes `Waiting for Input`; running and starting sessions become `Working`; session errors become `Blocked / Error`; ready, idle, or stopped turns become completed or idle states according to the available turn facts.

### Configuration

T3 Code normally stores its state under `~/.t3`. Override the base directory only when T3 uses another location:

```bash
sidepulse integrations configure t3code --base-dir ~/.t3
sidepulse integrations configure t3code --environment-id local
```

Clear overrides:

```bash
sidepulse integrations configure t3code --clear-base-dir
sidepulse integrations configure t3code --clear-environment-id
```

### Ownership and safety

SidePulse opens `userdata/state.sqlite` with SQLite URI `mode=ro`, enables `PRAGMA query_only`, applies a short busy timeout, verifies required tables and columns, and caps the result at 512 active, non-archived threads. It never writes the database, invokes T3 commands, reads T3 authentication material, changes a thread, or dispatches provider actions.

Additive columns are accepted. Missing required columns fail closed as an unsupported schema. A failed or busy refresh retains the prior snapshot and marks its projected rows stale instead of replacing known-good state with fabricated values.

The current upstream projection does not expose pull-request metadata. SidePulse therefore does not claim T3 pull-request compatibility or provide T3 mutation actions.

## Settings and compatibility

Integration settings are stored at:

```text
${XDG_CONFIG_HOME:-~/.config}/sidepulse/integrations.json
```

The document is versioned, preserves unknown fields, rejects concurrent replacement, and becomes read-only when written by a newer SidePulse version. A malformed existing document is preserved and refused rather than silently replaced.

The packaged compatibility manifest is `sidepulse.resources/integration_compatibility.json`. It records the exact reviewed upstream commit, protocol fingerprint, minimum version, maximum tested version, fixture version, and connection mode. The CLI exposes this information through `sidepulse integrations status --json`.

Current reviewed compatibility:

| Integration | Minimum | Maximum tested | Mode |
| --- | ---: | ---: | --- |
| T3 Code | 0.0.33 | 0.0.33 | `sqlite-readonly-v1` |
