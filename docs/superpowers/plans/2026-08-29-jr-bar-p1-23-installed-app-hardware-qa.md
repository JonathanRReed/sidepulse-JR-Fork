# JR Bar P1.23 Installed-App and Hardware QA Plan

Status: closed with direct failures and concrete blockers on 2026-08-29.

## Objective

Close roadmap item 23 with direct evidence from the installed product and any
available physical SidePulse hardware. Cover clean install, upgrade,
permissions, settings persistence, menu, Agent Browser, Usage Center, Alcove,
Screen Bar, Pro, Dot, sleep, wake, lid, DND, low power, device removal,
network failure, and recovery. Do not infer those results from source tests.

## Authority and Safety Boundary

- Begin with read-only discovery of app bundles, running processes, signatures,
  permissions, connected devices, and existing evidence.
- Do not replace the installed app, run an installer, kill the user's running
  app, reset settings, grant or revoke permissions, flash hardware, sign,
  notarize, publish, or release without a separately authorized step.
- Preserve the dirty worktree. Packaging and installed candidates must be tied
  to an exact source identity before their results are attributed to this
  roadmap work.
- Record unavailable hardware, TCC, signing, or destructive clean-install
  paths as explicit evidence gaps, not passing results.

## Parallel Read-Only Inventory

1. Resolve installed and running app identity, version, source relationship,
   signature, Gatekeeper, and available safe interaction surfaces.
2. Resolve connected Pro, Dot, USB, serial, and virtual Screen Bar targets,
   relevant permissions, discovery code paths, and existing hardware scripts.
3. Map the real-app interaction checklist for keyboard and VoiceOver traversal,
   Reduce Motion, previews and effects, Notification Center, failure states,
   recovery, and persistence.

## Execution Order

1. Consolidate the three inventories into an evidence matrix with exact
   candidate and hardware identities.
2. Run non-mutating installed-app smoke checks that cannot disturb active user
   work or persisted settings.
3. Run reversible UI checks only when the active candidate is attributable and
   the test does not require a permission or credential mutation.
4. Run hardware and lifecycle cases only for devices actually present and with
   a rollback path for every changed setting.
5. Recheck persisted state, app health, device recovery, and source identity
   after each mutating case.
6. Record direct passes, failures, and external blockers separately. Rerun the
   appropriate source gate only if P1.23 exposes a source defect that is fixed.

## Closure Contract

P1.23 closes only when every roadmap case has either a direct installed-app or
physical-device receipt, or a concrete blocker with the missing artifact,
device, permission, or authority named. Source tests, an installable package,
an installed older build, and virtual Screen Bar output do not substitute for
one another.

P2.24, the explicit application composition root, does not begin until this
receipt is complete.

## Closure Receipt

### Installed candidate

- The only live installed bundle was
  `/Users/jonathanreed/Applications/SidePulse.app`, version `0.5.0`, bundle ID
  `io.sidepulse.app`, executable SHA-256
  `da5726b80c62ba642815926a4081d93dfb09d2f757562db05ad6cf70912f9156`,
  signed by team `AJ9VWBRNZN`, and running as launchd PID 90832.
- Its signing timestamp was 2026-08-27, before the current P1.22 and P1.23
  source. Its bundle display name was still `SidePulse`, although its
  application menu said `Quit JR-BAR`. It cannot prove the current source.
- Deep code-sign verification passed, but Gatekeeper rejected it as
  `Unnotarized Developer ID`; it had no stapled ticket and no package receipt.
  Clean-install and upgrade evidence therefore failed closed.
- The installed `doctor` command reported a valid packaged import and
  signature, installed launch agent, private paths, healthy negotiated
  sources, bounded worker and timer registries, one connected device, and
  unavailable Alcove observation. `integrations status --json` was readable
  and reported T3 Code disabled.

### Runtime failure

- The live process had a 9,352 MB physical footprint, including 7,459 MB
  classified as Mach messages, after roughly one day and twenty hours of
  uptime. A short follow-up window was flat, but still processed about 339
  Mach messages and 777 Mach system calls per second.
- Unified logging recorded 3,062 CoreDisplay master-port lookup failures in a
  30-minute window. Short CPU observations included 16 to 18 percent while
  idle. This installed candidate fails the runtime gate.
- The current source does not use the old CoreDisplay reader. A 1,500-read,
  21-second current-source DisplayServices probe completed with all reads
  available, a stable 27 to 28 MB footprint, no Mach-message category, and no
  stderr output. The focused brightness, scheduling, observer, notification,
  and callback-lifecycle slices passed 191 tests plus 3 subtests. This is
  source evidence that the stale installed path has been replaced, not a
  repaired installed-candidate receipt.

### Physical device

- One physical SidePulse Pro was mounted at `/Volumes/SidePulse`. Bounded
  current-source discovery and stable identity inventory both classified it
  as a connected Pro from its firmware status. No Dot was connected.
- The live installed app continued to update `LEDS.LED`. The observed program
  changed during the read-only inspection and passed the current eight-LED
  firmware parser at 429 bytes and 3 lines. This is a direct Pro output
  receipt, although it remains tied to the stale installed bundle.
- The hardware release script refused to run without `--confirm-write`, exit
  2. No explicit write, eject, unplug, reinsert, flash, pair, or permission
  mutation was performed.

### Case matrix

| Roadmap case | Result | Direct receipt or blocker |
|---|---|---|
| Clean install | Blocked | No exact current signed and notarized PKG; replacing an installed app was not authorized in this tranche |
| Upgrade | Blocked | No attributable current package, package receipt, or authorized replacement of the running app |
| Permissions | Partial | Focus roster was readable, but TCC database inspection was denied and Notification, Screen Recording, Calendar, and Reminders permission mutations were not authorized |
| Settings persistence | Partial | Settings and provider configuration hashes remained stable through read-only CLI and Computer Use checks; no write-and-relaunch round trip was authorized |
| Menu | Partial | The installed application menu exposed `Quit JR-BAR`; the status-item menu was not exposed by the Computer Use accessibility tree |
| Agent Browser | Blocked | Status-item entry point was inaccessible to the UI tool, and the installed build predates current source |
| Usage Center | Blocked | Same installed-candidate and status-item limitation |
| Alcove | Failed for candidate | Installed doctor reported Alcove observation unavailable |
| Screen Bar | Partial | Enabled in persisted settings and linked to hardware, but its non-activating overlay was not exposed by the UI tool |
| Pro | Passed for attached hardware | Physical Pro discovered, classified, actively updated, and its observed LED program passed the firmware parser |
| Dot | Blocked | No physical Dot was connected |
| Sleep, wake, and lid | Blocked | Exercising these states would interrupt the owner Mac; the stale app also held active caffeinate assertions |
| DND | Partial | Seven configured Focus modes and no active mode were readable; toggling a real mode would change system state |
| Low power | Blocked | Mac was on AC at 100 percent; changing power state was outside the read-only pass |
| Device removal and recovery | Blocked | Requires physical eject, removal, and reinsertion |
| Network failure and recovery | Blocked | Requires changing live connectivity or provider access |
| Keyboard, VoiceOver, and Reduce Motion | Blocked for installed app | The installed build predates P1.22; VoiceOver and Reduce Motion changes require live system interaction |

This closes the ordered QA tranche with direct failures and explicit blockers,
not with a release-readiness claim. A future current candidate must rerun every
blocked or partial row before release.
