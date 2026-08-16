# T3 Code and CodexBar integrations

SidePulse can consume read-only status from T3 Code and display-only usage data from CodexBar. Both integrations are disabled by default and configured outside the primary agent-monitor settings document.

## Commands

Show configuration and the packaged compatibility window:

```bash
sidepulse integrations status
sidepulse integrations status --json
```

Enable or disable an integration:

```bash
sidepulse integrations enable t3code
sidepulse integrations disable t3code
sidepulse integrations enable codexbar
sidepulse integrations disable codexbar
```

Run one bounded compatibility probe:

```bash
sidepulse integrations probe t3code
sidepulse integrations probe t3code --json
sidepulse integrations probe codexbar
sidepulse integrations probe codexbar --json
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

- project and thread IDs;
- project and thread titles;
- underlying provider and provider-instance identity;
- provider thread ID when available;
- model and reasoning-effort selection;
- runtime and interaction mode;
- branch and worktree path;
- active/latest turn identity;
- session status and failure presence;
- pending approval, user-input, and actionable-plan counts;
- the official `t3code://threads/<environment>/<thread>` deep link when an environment ID is configured.

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

SidePulse opens `userdata/state.sqlite` with SQLite URI `mode=ro`, enables `PRAGMA query_only`, applies a short busy timeout, verifies required tables and columns, and caps the result at 512 active, non-archived threads. It never writes the database, invokes T3 commands, reads T3 authentication material, changes a thread, or dispatches a provider command.

Additive columns are accepted. Missing required columns fail closed as an unsupported schema. A failed or busy refresh retains the prior snapshot and marks its projected rows stale instead of replacing known-good state with fabricated values.

The current upstream projection does not expose pull-request metadata. SidePulse therefore does not claim T3 pull-request compatibility or provide T3 mutation actions.

## CodexBar

### Supported data

SidePulse parses CodexBar dashboard-v1 rows for:

- provider order, display name, enabled state, and data source;
- provider status level and label;
- account display identity and plan;
- primary and secondary usage windows, remaining percentage, and reset time;
- multi-account rows;
- credits remaining and unit;
- today and trailing-30-day cost;
- per-provider error state and freshness.

The Why/diagnostics panel reports connection mode, CodexBar version, provider count, error count, freshness, and the most constrained remaining quota.

### Configuration

Identity is redacted by default. Full identity is an explicit local choice:

```bash
sidepulse integrations configure codexbar --identity redacted
sidepulse integrations configure codexbar --identity full
```

Connection modes:

```bash
sidepulse integrations configure codexbar --connection-mode auto
sidepulse integrations configure codexbar --connection-mode serve
sidepulse integrations configure codexbar --connection-mode dashboard
```

`auto` prefers a supervised loopback child and falls back to the one-shot `codexbar dashboard` command. `serve` requires the child path to work. `dashboard` always uses one-shot snapshots.

### Ownership and safety

CodexBar remains the credential, account, cookie, OAuth, provider-source, and accounting owner. SidePulse does not copy CodexBar’s configuration, inspect its Keychain entries, import browser cookies, refresh credentials, or invoke account-switching commands.

For supervised mode, SidePulse:

- binds CodexBar to `127.0.0.1` only;
- creates a 256-bit ephemeral bearer token;
- passes the token only through `CODEXBAR_DASHBOARD_TOKEN`, never an argv flag;
- sends the token only to `/dashboard/v1/snapshot`;
- does not enable non-loopback access or `--allow-plain-http`;
- forwards a small allowlist of process context rather than the complete parent environment;
- bounds startup, command, HTTP, stdout, stderr, provider, account, window, and JSON sizes;
- refuses redirects, duplicate JSON keys, unsupported schema versions, malformed provider rows, and duplicate provider IDs.

The one-shot path calls only the documented noninteractive dashboard command. It never invokes CodexBar cookie refresh, cache mutation, reauthentication, or provider credential commands.

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
| CodexBar | 0.37.2 | 0.50.0 | `dashboard-v1` |

A newer untested version may continue to work when its required schema remains compatible, but it is not represented as release-verified until the compatibility fixtures and manifest are updated.
