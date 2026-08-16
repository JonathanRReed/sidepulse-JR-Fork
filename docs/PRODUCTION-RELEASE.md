# SidePulse production release gate

A release tag may be published only after the authoritative macOS gate passes from a clean `main` checkout that exactly matches `origin/main`.

## Required evidence

Set the signing identities and notarization profile used by `packaging/build_macos_pkg.sh`:

```bash
export APP_SIGN_IDENTITY='Developer ID Application: …'
export INSTALLER_SIGN_IDENTITY='Developer ID Installer: …'
export NOTARY_PROFILE='sidepulse-notary'
```

Create an Instruments-backed JSON evidence file with these fields:

```json
{
  "warm_launch_ms": 450,
  "menu_open_p95_ms": 40,
  "pane_switch_p95_ms": 80,
  "longest_main_thread_task_ms": 12,
  "idle_cpu_hidden_percent": 0.5,
  "idle_cpu_static_bar_percent": 1.0,
  "idle_cpu_motion_percent": 2.5,
  "measurement_duration_seconds": 300,
  "instruments_trace_reviewed": true,
  "menu_tracking_io_observed": false
}
```

The measurements must come from the signed candidate on the release Mac after a five-minute warm period. Review the Instruments trace for main-thread subprocesses, filesystem walks, network requests, hardware writes, fsyncs, and unexpected AppKit work during menu tracking.

## Installer ownership

The signed package installs the application payload and an owned `sidepulse` CLI link only. Package scripts do not install provider hooks, a user LaunchAgent, the privileged sleep helper, the eject guard, T3 Code integration, or CodexBar integration. Those are external mutable state and cannot be transactionally rolled back by Installer without risking pre-existing user setup.

The ordinary user completes integrations from SidePulse’s first-run setup or explicit CLI commands. The release gate exercises the explicit installed `status-bar start` command after package installation before it checks the LaunchAgent. This keeps package installation reversible while still testing the installed integration path.

## Run the gate

Connect the required physical hardware and preserve an existing settings file for the installed-upgrade check. The default gate requires both SidePulse Pro and SidePulse Dot. Override `SIDEPULSE_REQUIRED_HARDWARE` with `pro`, `dot`, or `any` only for a documented hardware-matrix exception.

```bash
export SIDEPULSE_PERFORMANCE_EVIDENCE="$PWD/performance-evidence.json"
export SIDEPULSE_HARDWARE_CONFIRM=1
export SIDEPULSE_RUN_INSTALLED_UPGRADE=1
./scripts/verify_macos_release.sh
```

The gate runs the full test suite, package checks, clean-wheel install, performance-budget validation, Developer ID signing, notarization, stapling, Gatekeeper assessment, bundle closure inspection, reversible hardware writes, a real package installation, explicit installed LaunchAgent setup, settings preservation, signing-team continuity, and installed `sidepulse integrations status --json` smoke validation.

Before authorizing the release gate, enable T3 Code or CodexBar only when the release Mac has representative local data. Run their bounded probes separately and preserve the output with the release evidence:

```bash
sidepulse integrations status --json
sidepulse integrations probe t3code --json
sidepulse integrations probe codexbar --json
```

An unavailable optional third-party installation is not a failure for the core package. A configured integration that is present but violates its required schema, process, freshness, or installed-command contract blocks the associated compatibility claim.

## Publish

`publish_release.sh` creates a draft release against the exact verified commit, uploads checksummed artifacts, and publishes only after every upload succeeds. A failed publication deletes the draft release and its server-side tag.

```bash
./scripts/publish_release.sh
```

Do not create or push a release tag manually. Do not treat a portable or unsigned package as a production candidate.
