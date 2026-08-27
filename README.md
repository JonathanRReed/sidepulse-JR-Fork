# JR-BAR (formerly SidePulse)

A macOS menu-bar app that turns AI-agent activity into ambient light —
on SidePulse LED hardware (the Pro in a MacBook's SD slot, the Dot on
USB-C) and on an on-screen light bar that hugs the notch. The rename to
JR-BAR is display-name-first: bundle identifiers, file paths, and the
`sidepulse` CLI keep the old name for now.

When Claude Code or Codex is working, the lights breathe in that
session's color. When a task finishes, they sweep green. When an agent
is blocked waiting on *you*, they turn amber and escalate — light,
menu-bar flash, chime, and optionally a webhook that can reach your
phone — until you answer. You stop tabbing over to check on agents;
you glance at the light.

This is a fork of
[inteliwear/sidepulse](https://github.com/inteliwear/sidepulse) that
grows the original device companion into a universal status indicator
for the Mac. It is fully divergent — hardware stays first-class, new
signal surfaces, deep customization behind good defaults — with no
intention of merging back upstream; upstream work is reviewed and
ported behavior by behavior instead.

## What it does

- **Watches agent sessions** through provider hooks — Claude Code,
  Codex, Devin, Grok, Cursor, Hermes, OpenClaw, OpenCode, Antigravity,
  and Kiro. It knows the
  difference between a main session and its sub-agent workers, between
  "finished and you've seen it" and "finished while you were away",
  and between a real blocked-on-you request (permission prompt, error)
  and a turn that merely ended with a question — only the real ones
  escalate.
- **Renders status everywhere you look**: the physical LEDs, the
  Screen Bar around the notch, and the menu-bar icon with a dropdown
  of live sessions (click one to jump to its terminal — or click the
  Screen Bar itself while an ask is live).
- **Layers Mac signals on top**: calendar and reminder glows,
  severe-weather warnings, battery, and quota
  alerts share one precedence ladder. A blocked agent always outranks
  the rest; per-Focus and per-device policies decide what else gets
  through (a Dot can be pinned to one provider, or to asks only).
- **Tracks usage and cost**: today's tokens with approximate cost and
  cache savings per provider, weekly limit percentages (including
  Anthropic's own usage endpoint, opt-in), and daily/hourly graphs
  from a week up to a year.
- **Leaves the desk when needed**: a webhook bridge POSTs JSON moments
  — agent blocked for minutes, task completed, quota crossed, severe
  weather — to ntfy, Home Assistant, or anything with a URL.

## Quick start

```sh
python3 -m pip install -e .
sidepulse setup
```

`sidepulse setup` walks through hook installation per provider,
installs SidePulse Pro Eject Prevention, and writes the status-bar
LaunchAgent so the menu-bar app starts now and at login. (The sealed
production "SidePulse.app" comes from the signed PKG built by
`packaging/build_macos_pkg.sh`.)
The menu-bar app's own Setup window covers the same ground with
buttons. Everything works with zero granted permissions; individual
features ask for what they need when you turn them on:

| Permission | Unlocks | Asked when |
| --- | --- | --- |
| Full Disk Access | Focus-mode reactions (dim/off/profile per Focus) | You enable Focus features |
| Calendar / Reminders | Event and reminder glows | You enable those signals |
| Screen Recording | The Screen Bar matching Alcove's live capsule width | Automatic if granted; quietly skipped otherwise |

## The Screen Bar

A light bar that wraps the MacBook notch and mirrors the LEDs —
useful when the hardware is out of sight or you have none. It measures
the real notch from screen pixels, coexists with
[Alcove](https://henrikruscon.com/alcove) by drawing a bracket around
it (matching Alcove's visible capsule width automatically), can run
pitch-black with only the moving signal visible, and answers clicks
and hovers: click during an ask to jump to the session, dwell for a
quota peek. Optional wing-tip gauges keep a quota ember and an
unseen-done dot in your peripheral vision.

## Hardware

The devices mount as disk drives; everything renders by writing a
small LED program to `LEDS.LED` (the DSL is in
[`LEDS_FORMAT.md`](LEDS_FORMAT.md), and writes are atomic — an eject
mid-write can't leave the firmware a torn program). Per-device:
display choice (agent / battery / timer / studio / quota runway),
brightness with auto-brightness, white-point calibration with
day/night/travel profiles, resting glow, provider pinning, and signal
muting. The Studio pane lets you write programs by hand, keep a shelf
of saved looks, and burn one into `INIT.LED` so the hardware boots
wearing your colors.

## Odds and ends worth knowing

- **Timer**: dropdown presets drain the bar as a countdown, can run a
  Shortcut at start and end (Focus on with the drain, off when it
  finishes), and turn a deepening ember when you run over.
- **Night warmth**: eases green and blue down from 7 PM to 7 AM,
  composed over each device's calibration.
- **Color by project**: sessions in the same repo share a hue family,
  providers told apart by lightness.
- **Quiet hour, per-Focus signal policies** (all / asks only /
  silent), and a **quota sunrise** sweep the moment a limit window
  resets.
- Engineering: no SD-card I/O, subprocess forks, or sqlite on the main
  thread; a change-gated Screen Bar (60fps active, 30fps resting breathe) with 60Hz-capped WASM sampling;
  6,000+ checks in the verification gate; corrupt settings are preserved for recovery,
  never silently reset. Architecture notes live in
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), the historical build
  ledger in [`docs/archive/FORK-ROADMAP.md`](docs/archive/FORK-ROADMAP.md).

## Credits

Many monitoring semantics and usage-tracking techniques were adopted,
with citations in the code, from studying
[T3 Code](https://github.com/pingdotgg/t3code) and
[CodexBar](https://github.com/steipete/CodexBar).

Everything below is the upstream project's reference documentation:
the CLI, the LED format, and the battery tools, all of which this fork
keeps working.

---

`sidepulse` is the command-line and macOS companion project for
[SidePulse](https://sidepulse.io).

They can display the status of an AI agent, battery level, or other system
signals.

| <img src="https://raw.githubusercontent.com/inteliwear/sidepulse/main/media/sidepulse-pro.jpg" alt="SidePulse Pro glowing pink in a MacBook Pro SD card slot" width="400"> | <img src="https://raw.githubusercontent.com/inteliwear/sidepulse/main/media/sidepulse-dot.jpg" alt="SidePulse Dot glowing green in a MacBook USB-C port" width="400"> |
|:---:|:---:|
| **SidePulse Pro** — eight-LED SD card device for MacBook Pro. | **SidePulse Dot** — tiny two-LED USB-C device. |

Agent status, at a glance:

https://github.com/user-attachments/assets/9de119ac-7b55-467f-8517-6c5f1570c1af

The device mounts as a disk drive. You can control the LEDs by writing to `LEDS.LED`.

The LED control DSL is described in [`LEDS_FORMAT.md`](LEDS_FORMAT.md).

### TLDR
```sh
python3 -m pip install -e .
sidepulse setup
```

Write an LED program directly to a mounted SidePulse Pro or SidePulse Dot device:

```sh
sidepulse write "off\n#ff3a00 1.6s pulse\nrepeat"
```

The CLI auto-detects mounted devices under `/Volumes` by looking for a
SidePulse Pro/SidePulse Dot-style volume name or an existing `LEDS.LED`. If more than
one device is possible, pass the mounted folder or file explicitly:

```sh
sidepulse write "off\n#ff3a00 1.6s pulse\nrepeat" --device /Volumes/SidePulsePro
sidepulse write "off" --device /Volumes/SidePulsePro/LEDS.LED
```

The writer decodes simple escapes such as `\n`, then enforces the controller's
512-byte and 20-line limits before writing the LED control file.

## Battery LEDs

Show the current Mac battery state:

```sh
sidepulse battery status
sidepulse battery status --json
```

Mirror battery level to a mounted SidePulse Pro/SidePulse Dot:

```sh
sidepulse battery leds
sidepulse battery leds --once --dry-run
sidepulse battery leds --device /Volumes/SidePulsePro --full-watts 140
```

SidePulse Pro uses all eight LEDs as a battery bar. At 50%, LEDs 0-3 are filled;
when charging, LED 4 is the pulsing frontier LED. Live updates ease the whole
strip into its new base state, then trigger one frontier pulse. The app owns
the animation cadence by rewriting that one-shot pulse; the device does not run
a repeated charging loop. Pulse length and rewrite frequency are based on
charger wattage divided by the laptop's full-speed wattage baseline, so slow
chargers produce occasional short blinks and full-speed chargers produce a
steady pulse.

Save the status-bar LED display preference:

```sh
sidepulse battery configure --display battery
sidepulse battery configure --display agent
sidepulse battery configure --full-watts auto
sidepulse battery configure --show-on-power-change yes --power-change-preview-seconds 7
```

## sidepulse

`sidepulse` includes a companion menu-bar app for macOS that controls
SidePulse Pro and SidePulse Dot.

### Main Functionality

#### AI Agent Monitoring

SidePulse can monitor AI agents such as Claude, Devin, Codex, and Grok through hooks, then
translate the current agent state into a small, glanceable LED status.

Agent status modes:

| Mode | Meaning | LED pattern |
| --- | --- | --- |
| Idle / Ready | The agent is available and not currently running a task. | Very dim idle pulse. |
| Working | The agent is thinking, generating, or otherwise actively processing. | Cyan rolling animation. |
| Tool Running | A shell command, API call, or external tool is in progress. | Cyan rolling animation. |
| Waiting for Input | The agent needs a user decision, approval, or additional context. | Slow amber pulse. |
| Long Task Progress | A longer job has measurable progress. | Cyan rolling animation. |
| Blocked / Error | The agent cannot continue, a tool failed, or a recoverable error needs attention. | Slow amber pulse. |
| Completed | The agent finished successfully. | Solid green. |

When multiple states are active, SidePulse should show the most actionable
mode first: Blocked / Error, Waiting for Input, Tool Running, Long Task
Progress, Working, then Idle / Ready.

For multiple agents, SidePulse aggregates their statuses into one global
display state. Each agent reports its own mode, and SidePulse renders the
highest-priority active mode across all non-stale agents. This keeps the device
useful at a glance: if any agent is blocked or waiting, the LEDs show that
actionable state instead of trying to show every agent separately.

Aggregation priority:

| Priority | Mode | Aggregated behavior |
| --- | --- | --- |
| 1 | Blocked / Error | Show immediately if any agent is blocked or has errored. |
| 2 | Waiting for Input | Show if any agent needs user input and no agent is blocked. |
| 3 | Tool Running | Show if any agent is running a tool and no higher-priority state is active. |
| 4 | Long Task Progress | Show the most recent or furthest-progressing long task. |
| 5 | Working | Show while one or more agents are actively processing. |
| 6 | Completed | Show briefly when the latest active agent completes successfully. |
| 7 | Idle / Ready | Show only when all known agents are idle or no fresh agent status exists. |

Agent statuses should include a timestamp. SidePulse should ignore stale
statuses after a short timeout so disconnected or finished agents do not hold
the display indefinitely.

#### Agent Monitor Library

The `sidepulse` Python package collects and normalizes local AI agent hook
events. The macOS status-bar app receives hook events through a lightweight
local Unix socket, keeps the latest agent states in memory, and writes only a
small `latest.json` restart snapshot plus provider JSONL debug logs. The app
does not rescan historical logs or transcripts on every refresh.

The package can also mirror the aggregate state to a mounted SidePulse Pro or
SidePulse Dot by writing the current LED program to `LEDS.LED`.

The monitor supports every registered provider — Codex, Claude, Devin,
Grok, Cursor, Hermes, OpenClaw, OpenCode, Antigravity, and Kiro
(`sidepulse agent-monitor doctor` reports each provider's detected
config and log paths). The founding four:

| Provider | Config | Detected log |
| --- | --- | --- |
| Codex | `~/.codex/config.toml` | `${XDG_STATE_HOME:-~/.local/state}/sidepulse/agent-monitor/codex.jsonl` |
| Claude | `~/.claude/settings.json` | `${XDG_STATE_HOME:-~/.local/state}/sidepulse/agent-monitor/claude.jsonl` |
| Devin | `~/.config/devin/config.json` | `${XDG_STATE_HOME:-~/.local/state}/sidepulse/agent-monitor/devin.jsonl` |
| Grok | `~/.grok/hooks/sidepulse.json` | `${XDG_STATE_HOME:-~/.local/state}/sidepulse/agent-monitor/grok.jsonl` |

Each provider adapter only adds SidePulse's own hook commands. Existing hook
entries, including Agent Deck entries, stay in place. Before a changed existing
configuration is written, SidePulse creates a timestamped backup beside it.
Use `sidepulse agent-monitor uninstall <provider>` to remove only SidePulse
hooks, or restore that backup if you need to roll back the complete file.

To add a future provider, add a `ProviderSpec` for its identity, supported
event set, and configuration detector; add its adapter functions to both
`INSTALLERS` and `UNINSTALLERS`; and add preservation, detection, and CLI
coverage. The detector reports the config and log paths to `doctor`; the
adapter must preserve unrelated configuration while adding and removing only
SidePulse hooks.

For CLI snapshots, debugging, or recovery after missed hook events, the
file-based monitor can optionally read recent local transcripts as a fallback:

- Codex: `~/.codex/sessions/**/*.jsonl`
- Claude: `~/.claude/projects/**/*.jsonl`

Transcript monitoring is off by default and can be enabled in Settings. It can
catch active threads even when hook events are stale or missed. Claude
transcript files can be touched after their embedded event timestamps stop
moving, so a recent transcript mtime is treated as a Working heartbeat only
when the latest embedded event was already active. File mtimes never resurrect
a terminal `Stop` / `Completed` session. Internal Codex helper/suggestion
transcripts are ignored so app background work does not look like one of your
agents.

By default the monitor stores runtime logs under
`~/.local/state/sidepulse/agent-monitor/`, following the XDG state directory
convention. Set `XDG_STATE_HOME` to place them somewhere else.

Install locally for the `sidepulse` CLI:

```sh
python3 -m pip install -e .
```

This also installs the Cocoa dependencies for the macOS status-bar app.

Set up this Mac explicitly after package install:

```sh
sidepulse setup
```

`sidepulse setup` installs or refreshes hooks for every registered provider, installs
SidePulse Pro Eject Prevention, writes the status-bar LaunchAgent, starts both helpers
immediately, and enables them at login. This is intentionally an explicit
command instead of a `pip install` side effect. To set up only one provider,
name it: `sidepulse setup codex`, `sidepulse setup claude`,
`sidepulse setup cursor`, and so on. Every hook command is probe-run before
any provider config is written; a command that cannot run is refused with a
clear error. Existing hook entries are preserved for every setup
command.
To skip the status-bar app but still install hooks and SidePulse Pro Eject Prevention, use
`sidepulse setup --no-status-bar`.

SidePulse Pro Eject Prevention keeps the built-in SD reader attached after
macOS hibernate or lock-screen mount refusals. By default setup installs it
system-wide when already running with system permissions, otherwise as a
per-user LaunchAgent:

```sh
sidepulse setup --sd-eject-guard-scope auto
sidepulse setup --sd-eject-guard-scope user
sidepulse setup --sd-eject-guard-scope system --no-status-bar
```

The system scope requires the command to already have system install
permissions.

Manage SidePulse Pro Eject Prevention directly:

```sh
sidepulse sdejectguard start
sidepulse sdejectguard stop
sidepulse sdejectguard uninstall
sidepulse sdejectguard logs
sidepulse sdejectguard start -it
```

`start -it` runs the guard in the current terminal for interactive debugging.

On Homebrew Python, use the user-site install form:

```sh
python3 -m pip install --user --break-system-packages -e .
ln -sf "$(python3 -m site --user-base)/bin/sidepulse" ~/.local/bin/sidepulse
```

### macOS installer

A signed and notarized PKG release can be built with
[`packaging/build_macos_pkg.sh`](packaging/build_macos_pkg.sh). See
[`packaging/README.md`](packaging/README.md) for the required Developer ID
certificates and notarization profile.

Check the current hook configuration:

```sh
sidepulse agent-monitor doctor
```

Install or refresh the monitor hooks:

```sh
sidepulse agent-monitor install
sidepulse agent-monitor install codex
sidepulse agent-monitor install claude
sidepulse agent-monitor install devin
sidepulse agent-monitor install grok
```

Any registered provider name works the same way (`cursor`, `hermes`,
`openclaw`, `opencode`, `antigravity`, `kiro`).

Each hook invokes a small, standard-library-only Python entry point. It writes
the event to the monitor log and then makes a short best-effort local socket
delivery to the status-bar app.

Show current aggregated status:

```sh
sidepulse agent-monitor status
```

Watch a live dashboard of recently active agents:

```sh
sidepulse agent-monitor live
```

The dashboard refreshes every second and shows agents updated in the last hour
by default. Use `--recent-seconds` to change that window, or `--all` to
include stale/older sessions:

```sh
sidepulse agent-monitor live --recent-seconds 120
sidepulse agent-monitor live --all
```

By default, `Tool Running` events are not time-limited, so genuinely long tools
remain visible. If a provider drops completion hooks and you want protection
against stale tool starts, set `--tool-running-timeout`.

`PostToolUse` means the tool returned, not that the whole turn is finished. The
monitor keeps it as Working for a short settling window while the assistant
writes the response, then treats it as Done if no newer hook event arrives. This
prevents a missed final `Stop` event from leaving the status bar stuck on
Working.

`Completed` remains visible for 20 minutes so the status bar and LEDs can show
Done long enough to be noticed. After that it drops out instead of counting as
an active session for the full stale window, and the LEDs return to the very
dim Idle pattern. Idle/session-start records also do not count as active
sessions.

Status detection is strongest when the agent tells the monitor its intended
handoff state explicitly. A final assistant message can include a hidden marker
line:

```text
<!-- sidepulse:ask -->
<!-- sidepulse:done -->
<!-- sidepulse:working -->
<!-- sidepulse:blocked -->
<!-- sidepulse:idle -->
```

Explicit markers win over text heuristics. If no marker is present, the monitor
falls back to provider events and then to conservative question detection in the
final assistant message. Casual closing questions such as "Anything else?" are
treated as Done unless the agent emits `<!-- sidepulse:ask -->`; concrete
follow-ups such as "Want me to push?" still count as Ask. Questions inside
markdown code spans or fenced code examples are ignored.

Codex `PermissionRequest` events are treated as Ask and remain sticky until the
matching tool command finishes. This prevents unrelated same-session activity
from hiding an approval prompt that is still waiting on the user.

For Claude, Devin, Codex, or Grok projects that should report this reliably, add
guidance like this to the relevant agent instructions:

```text
When your final response needs user input, approval, or a decision, include
`<!-- sidepulse:ask -->` as a final hidden marker line. When the work is complete
and no user response is needed, include `<!-- sidepulse:done -->`.
```

Mirror the aggregate agent status to the LEDs in a foreground process:

```sh
sidepulse agent-monitor leds
```

The LED mirror writes only when the aggregate display state changes. Use
`--once` to write the current state and exit, or `--dry-run` to inspect the LED
program:

```sh
sidepulse agent-monitor leds --once --dry-run
sidepulse agent-monitor leds --device /Volumes/SidePulseDot
```

SidePulse Dot programs are generated for two LEDs. SidePulse Pro programs are generated
for eight LEDs. The monitor detects this from the mounted device name and falls
back to the eight-LED SidePulse Pro layout if the name is unknown.

Remove monitor hooks:

```sh
sidepulse agent-monitor uninstall
sidepulse agent-monitor uninstall codex
sidepulse agent-monitor uninstall claude
sidepulse agent-monitor uninstall devin
sidepulse agent-monitor uninstall grok
```

Install and start the macOS status-bar app:

```sh
sidepulse status-bar
sidepulse status-bar start
```

This writes `~/Library/LaunchAgents/io.sidepulse.agentstatus.plist`, starts the
menu-bar app immediately, enables it at login, and mirrors the same aggregate
state to the LEDs. For debugging, run it in the foreground:

```sh
sidepulse status-bar start --foreground
```

On first launch, the status-bar app shows a SidePulse Setup window. It can:

- enable Run at Login;
- install or uninstall SidePulse Pro Eject Prevention, which keeps SidePulse Pro/SidePulse Dot available after sleep;
- open the one-time closed-lid sleep prevention installer in Terminal.

The Setup window can be reopened from the dropdown with `Setup...`.

The status-bar item shows one of four collapsed states:

| Label | Meaning |
| --- | --- |
| Idle | No recent active agent work. |
| Working | One or more agents are thinking, running tools, or progressing. |
| Done | The most recent active agent completed successfully. |
| Ask | An agent needs input, permission, or attention. |

Click the status-bar item to expand the recent session list. Click a session
row to open that agent using the remembered choice for that provider. Use the
session's Open Options row to choose and remember another opener, such as the
provider app, Terminal resume, or Claude Code in VS Code.

The dropdown also includes a checked `Connect to Device` item. A checkmark means
the status-bar app is actively connected to a mounted SidePulse Pro/SidePulse Dot target.
If both devices are mounted, the status-bar app prefers SidePulse Pro, then
SidePulse Dot. Click the item to disconnect and turn the LEDs off; click it again to
reconnect.

The dropdown and Settings window can switch the LEDs between agent status and
battery status. When agent status is selected, `Show Battery on Plug/Unplug`
can briefly show the battery animation for seven seconds after the power source
changes.

The Devices section also offers **Add Screen Bar**, an optional virtual
eight-LED device. It appears as a notch-shaped status-bar overlay that covers
the camera island/notch footprint and adds a straight 5 px LED band along the
bottom edge, or the corresponding top-center position on a display without a
notch. Each virtual LED blends across a three-LED footprint: centered on the
target LED, fading one LED width left and right. It shares the physical
device's status animations, display-mode selection, and per-device brightness
control. In classic mode everything the bar paints stays inside the measured notch
silhouette -- opaque housing, glow feathered to black before the corner
fillets -- and while an agent asks, a hover-reveal announcer pill names the
session (click jumps to it). The Screen Bar evaluates the same `LEDS.LED` programs with the
firmware/websim `sdled.wasm` engine, then AppKit only draws the returned RGB
frames.

Open `Settings...` from the dropdown to manage agent integrations. The settings
window can install or uninstall provider hooks. The transcript
checkboxes control the file-based CLI/debug fallback; the status-bar app gets
live updates from the local hook event socket. Settings are stored at
`${XDG_CONFIG_HOME:-~/.config}/sidepulse/agent-monitor/settings.json`.
Safe diagnostic export lives in the History pane; the old hook decision
log and its CSV/HTML exporters were deleted 2026-08-26.

The `Keep Awake With Lid Closed` menu section controls the stronger sleep
prevention policy:

| Choice | Behavior |
| --- | --- |
| Never | Do not use the closed-lid sleep override. |
| When Agents Work | Keep the Mac awake while agents are Working / Tool Running / Progressing, plus one five-minute grace window armed when work stops (rest-to-rest mode changes never re-arm it). |
| Always | Keep the closed-lid sleep override active while the status-bar app is running. |

The status-bar app still keeps the SidePulse Pro/SidePulse Dot volume active by touching
a `keepalive` file on each connected device at least once per minute. The
closed-lid policy uses the SidePulse sleep helper when it is installed. The PKG
installer sets this up automatically; source/dev installs can run the one-time
setup command:

```sh
sudo "$(command -v sidepulse)" status-bar install-sleep-helper
```

The helper is a narrow sudoers rule for exactly
`/usr/bin/pmset -a disablesleep 0|1`, so the status-bar app can toggle it
silently with non-interactive `sudo`. SidePulse uses this automatically for
`Keep Awake With Lid Closed` and only restores the setting if SidePulse changed
it. Remove the helper with:

```sh
sudo "$(command -v sidepulse)" status-bar uninstall-sleep-helper
```

Open `Settings...` to edit and preview the Lid Closed and Lid Open LED
animations. Animation programs use the same `LEDS.LED` syntax as
`sidepulse write`; device brightness is applied automatically before writing.

The app is also installed as a user LaunchAgent at
`~/Library/LaunchAgents/io.sidepulse.agentstatus.plist`.

Stop and remove the LaunchAgent:

```sh
sidepulse status-bar stop
```

Use it from another Python app:

```python
from sidepulse import AgentMonitor, LiveAgentMonitor

snapshot = AgentMonitor.from_default_sources().snapshot()
print(snapshot.aggregate.mode.value)
for status in snapshot.statuses:
    print(status.provider, status.mode.value, status.cwd)

live = LiveAgentMonitor()
```

Publish a hook-shaped event to the status-bar app from another local process:

```python
from sidepulse import send_hook_event

send_hook_event(
    "codex",
    {
        "logged_at": "2026-07-13T12:00:00Z",
        "event": {
            "hook_event_name": "Stop",
            "session_id": "example",
            "last_assistant_message": "Done.",
        },
    },
)
```

#### Audio Monitor Example

`examples/audio_monitor.py` turns microphone volume into a smooth LED level
bar. The LEDs stay dim at rest, run green through yellow to red, and brighten as
the audio level fills the bar.

Install the optional live-audio dependencies:

```sh
python3 -m pip install sounddevice numpy
```

Preview the meter in the terminal without touching a device:

```sh
python3 examples/audio_monitor.py --dry-run --terminal
```

Write to a mounted SidePulse Pro or SidePulse Dot:

```sh
python3 examples/audio_monitor.py --device /Volumes/SidePulsePro --terminal
python3 examples/audio_monitor.py --device /Volumes/SidePulseDot --terminal
```

List audio inputs or tune sensitivity:

```sh
python3 examples/audio_monitor.py --list-inputs
python3 examples/audio_monitor.py --device /Volumes/SidePulsePro --gain-db 8 --release 0.45
```

#### Battery Monitor

...

#### 
