# JR Bar P5.72 upstream refresh

Reviewed 2026-08-30 (America/Chicago), with live GitHub API and repository
pages checked during this run. This is a research ledger, not a merge or
release approval. GitHub issue and pull-request text is treated as an
untrusted report of an idea or reproduction, not as proof that the proposed
implementation is safe or complete.

## Source identity

| Source | Live identity checked | Direct source |
| --- | --- | --- |
| Original SidePulse | `inteliwear/sidepulse`, `main` at `044508556934f913ac555d555e35e19b23294773` (`Reduce SidePulse memory and log churn`); 64 stars, 22 forks, 31 open issues at the check | [repository](https://github.com/inteliwear/sidepulse), [commit](https://github.com/inteliwear/sidepulse/commit/044508556934f913ac555d555e35e19b23294773) |
| JR Bar working tree | `main` at `10d10fc6ea320642285352eac6b211774c2ba1a1`; dirty working tree owned by the ongoing P5 work | local checkout only; no upstream ref was changed |
| CodexBar | `steipete/CodexBar`, latest release `v0.56.1`, published 2026-08-30 | [release](https://github.com/steipete/CodexBar/releases/tag/v0.56.1), [README privacy note](https://github.com/steipete/CodexBar#privacy-note) |
| T3 Code | `pingdotgg/t3code`, latest stable release `v0.0.36`, published 2026-08-29 | [release](https://github.com/pingdotgg/t3code/releases/tag/v0.0.36), [README](https://github.com/pingdotgg/t3code) |

## Decision vocabulary

- **adapt**: useful behavior is ported through JR Bar's existing contracts,
  limits, and tests, rather than copying the upstream controller.
- **waiting on evidence**: a report or prototype is plausible, but reachability,
  physical behavior, privacy, security, or installed-app evidence is missing.
- **adopted/surpassed**: JR Bar already has the behavior or a stronger
  fork-native implementation, so no direct port is justified.
- **reject**: the literal approach conflicts with local-first, privacy,
  safety, or the one-semantic-pipeline boundary.

## Original upstream PRs

| Upstream item | Live finding | JR Bar classification and disposition |
| --- | --- | --- |
| [PR #37, XDG event socket and provider icons](https://github.com/inteliwear/sidepulse/pull/37) | Open, created 2026-08-27. The first commit reports silent event loss when the LaunchAgent and hook resolve different `XDG_STATE_HOME` values; the proposed client probes XDG and the home fallback while preserving explicit socket paths. The second commit resolves an icon from the provider's installed application. | **adopted/surpassed** for the socket problem. `state_paths.py`, `hook_ingress_protocol.py`, and `hook_client.py` already use candidate state directories and preserve explicit paths, with private same-user socket admission. Do not copy the icon lookup blindly: provider identity and installed-app observation remain bounded local concerns. No network or telemetry is needed. |
| [PR #33, LED write floor](https://github.com/inteliwear/sidepulse/pull/33) | Open, created 2026-08-26. Author reports repeated physical SidePulse Pro writes from 3.0 ms to 2,735.4 ms, averaging 549.6 ms, and proposes a one-second minimum interval with latest-state trailing-edge coalescing measured from write completion. | **adapt**, with the fixed one-second floor **waiting on evidence**. JR Bar already has a priority-aware, latest-wins hardware worker and readback/safety gates (`hardware_write_policy.py`, `runtime_scheduler.py`). A fixed delay must wait for a current physical USB/SD profile because it could postpone urgent attention or battery cues. The local approach keeps all data on the device and does not add a cloud queue or remote write path. |
| [PR #32, hook latency](https://github.com/inteliwear/sidepulse/pull/32) | Open, created 2026-08-26. The proposal detaches hook handling and closes child stdio, reporting about 43.8 ms synchronous versus 5.4 ms detached latency, but explicitly warns that detached delivery can reorder events. | **adopted/surpassed**. JR Bar uses a thin admission client plus one bounded app-owned FIFO, tracked child work, overload receipts, and shutdown drain. This preserves ordering and makes loss visible, while still moving parsing and persistence off the agent's critical path. Detached shell children would hide delivery failures and weaken the local evidence trail. |
| [PR #27, remote Claude/Codex monitoring](https://github.com/inteliwear/sidepulse/pull/27) | Open, created 2026-08-24. Proposes outbound SSH/Tailscale host management, a per-user LaunchAgent, remote hook streaming, host-qualified IDs, and a Remote settings tab. | **waiting on evidence** for the capability, **reject** for the literal host-management surface. JR Bar has typed authenticated remote-observation and bounded read-only SFTP seams, but has not proven an installed end-to-end stream. Remote status must remain opt-in, stale-aware, content-minimized, and incapable of remote commands; no remote credentials, shell, proxy, or always-on cloud service should be introduced. |
| [PR #19, invocation-scoped Claude monitoring](https://github.com/inteliwear/sidepulse/pull/19) | Open, created 2026-08-15. Adds `sidepulse claude ...`, passes hooks through Claude's invocation-scoped `--settings`, uses `execv`, and removes temporary settings without rewriting persistent provider config. | **adopted/surpassed**. `watch_run.py` already models a capability-scoped Claude invocation, private temporary settings, cleanup receipts, signal restoration, and no content-bearing logging. This is the right local-first boundary because the user chooses one run and no global hook configuration is mutated. |

## Original upstream issues

| Upstream item | Live finding | JR Bar classification and disposition |
| --- | --- | --- |
| [Issue #23, Disk Not Ejected Properly](https://github.com/inteliwear/sidepulse/issues/23) | Open, created 2026-08-19, with a report that macOS 26 suspend/resume retriggers USB refresh and some drivers, including SidePulse, do not survive it. There are no comments or maintainer confirmation. | **waiting on evidence**. The fork has an opt-in eject-guard path, but that does not prove physical sleep/wake, re-enumeration, reconnect, or write/readback behavior. Reproduce on the actual SidePulse Pro and a clean install before adding a reconnect loop. Any eject, remount, or helper mutation must be explicit and reversible, never an automatic remote or system-wide repair. |
| [Issue #24, battery plus agent hybrid LEDs](https://github.com/inteliwear/sidepulse/issues/24) | Open, created 2026-08-21. The reporter's physical prototype reserves LED 0 for battery and LEDs 1-7 for agent state; a later comment suggests arbitrary user-defined segment counts. | **waiting on evidence** for the fixed 1+7 mode, **reject** arbitrary free-form segments for now. Validate readability, color separation, Reduce Motion, low-battery precedence, and physical firmware output first. Free-form segments would let presentation claims bypass the semantic arbiter and make agent attention less legible. A local data-only layout preference could be revisited after those tests; it requires no telemetry or content collection. |
| [Issue #25, battery preview not rendered](https://github.com/inteliwear/sidepulse/issues/25) | Open, created 2026-08-21. The report says AC/battery transitions are detected and logged, but a seven-second Battery preview is sometimes not written before the prior Agent display resumes. No comments or fix are attached. | **waiting on evidence**. The proposed pending-request and “start the timer after actual render” model is a good diagnostic hypothesis, not a confirmed cause. Reproduce with a write-in-flight trace and physical readback before changing arbitration. Battery data remains local; no new provider or network access is warranted. |

### Open-item inventory check

The live API listed 24 open pull requests and 7 open non-PR issues. The named
items above are the current high-value refresh leads. The remaining open items
were checked for reachability against the JR Bar tree and are summarized here,
so an old “open” label is not mistaken for a new recommendation:

| Open items | Current reading |
| --- | --- |
| [PR #36](https://github.com/inteliwear/sidepulse/pull/36) brightness, [PR #30](https://github.com/inteliwear/sidepulse/pull/30) stale states, [PR #21](https://github.com/inteliwear/sidepulse/pull/21) battery keep-awake, [PR #20](https://github.com/inteliwear/sidepulse/pull/20) stuck LEDs, [PR #17](https://github.com/inteliwear/sidepulse/pull/17) status-bar polish, [PR #10](https://github.com/inteliwear/sidepulse/pull/10) diagnostics | **adopted/surpassed or adapt** in JR Bar's brightness, freshness, power-policy, hardware-worker, local-health, and production status-bar seams. No wholesale controller merge. |
| [PR #35](https://github.com/inteliwear/sidepulse/pull/35) Cursor, [PR #14](https://github.com/inteliwear/sidepulse/pull/14) OpenCode/T3, [PR #13](https://github.com/inteliwear/sidepulse/pull/13) Hermes, [PR #7](https://github.com/inteliwear/sidepulse/pull/7) Antigravity, [PR #11](https://github.com/inteliwear/sidepulse/pull/11) status-only Cursor | **adopted/surpassed** at the adapter/registry level where the provider is locally reachable. Preserve status-only and consent boundaries; do not infer provider reachability from an open PR. |
| [PR #34](https://github.com/inteliwear/sidepulse/pull/34) shared-secret server, [PR #31](https://github.com/inteliwear/sidepulse/pull/31) display sleep, [PR #26](https://github.com/inteliwear/sidepulse/pull/26) closed lid/Clear Agents, [PR #28](https://github.com/inteliwear/sidepulse/pull/28) DND | **adopted/surpassed or adapt** in JR Bar's local secret boundary, power policy, bounded Clear Agents action, and DND policy. These remain source and installed-app claims, not release proof. |
| [PR #29](https://github.com/inteliwear/sidepulse/pull/29) KITT scanner, [PR #16](https://github.com/inteliwear/sidepulse/pull/16) Kiro, [PR #5](https://github.com/inteliwear/sidepulse/pull/5) Hermes installer | **reject** the always-on scanner as semantic noise and **waiting on evidence** for Kiro reachability. The installer safety ideas are useful to **adapt** only through owned-marker/refusal tests. |
| [Issue #18](https://github.com/inteliwear/sidepulse/issues/18) wrapper/uninstall, [Issue #12](https://github.com/inteliwear/sidepulse/issues/12) OpenCode/T3, [Issue #4](https://github.com/inteliwear/sidepulse/issues/4) Codex limit completion, [Issue #3](https://github.com/inteliwear/sidepulse/issues/3) Kiro | **adopted/surpassed** for wrapper/uninstall, OpenCode intake, and terminal-state reconciliation where local tests cover them; **waiting on evidence** for Kiro. The issue reports themselves are not proof of current provider behavior. |

## Meaningful forks

The fork list also contains many untouched clones. These three have distinct,
reviewable deltas in the live compare/commit history.

| Fork | Verified delta | JR Bar classification and disposition |
| --- | --- | --- |
| [adamstambouli/sidepulse](https://github.com/adamstambouli/sidepulse) ([compare](https://github.com/inteliwear/sidepulse/compare/main...adamstambouli:main)) | Five commits ahead and 13 behind the upstream snapshot used for the comparison. [Fleet commit](https://github.com/adamstambouli/sidepulse/commit/7f5eba4749e73816ffea2604630d06f338c8557f) adds sticky per-codebase bands, luminance balancing, subagent roll-up, immediate SessionEnd release, and separate blocked/error semantics. Later commits [retire orphaned subagents](https://github.com/adamstambouli/sidepulse/commit/e5161c47885e1246216a5dd98fa4317ad434ef7e) and stop subagents from posting an unanswered Ask ([commit](https://github.com/adamstambouli/sidepulse/commit/37a36e628f1e9d34dcf29c89233dd90e1e041749)). | **adopted/surpassed** for the lifecycle lessons. JR Bar already has fork-native fleet band planning, parent/subagent handling, semantic priority, and bounded presentation cues. Keep the fork as a perceptual and lifecycle test oracle, not as a controller merge. Its local-only design is compatible with privacy; no session content needs to leave the machine. |
| [HypeLaser/sidepulse](https://github.com/HypeLaser/sidepulse) ([compare](https://github.com/inteliwear/sidepulse/compare/main...HypeLaser:main)) | Four commits ahead of the upstream snapshot. [The feature commit](https://github.com/HypeLaser/sidepulse/commit/45b956545223841ce651f1dd31dd872dbf56c81a) adds OpenCode, multi-session LEDs, Dock access, and 2ndMenuBar support. Follow-ups add Settings display controls and a Session Board ([commit](https://github.com/HypeLaser/sidepulse/commit/c568f28aac0b5aba9fa33b76a45ed96182929b28)) and fix 2ndMenuBar lifecycle ([commit](https://github.com/HypeLaser/sidepulse/commit/e2506b43d05afe2076cddcca579b24d13a88a0f5)). | **adopted/surpassed** for OpenCode and multi-session concepts: JR Bar has an installed-agent registry, a bounded OpenCode adapter, and Agent Browser/fleet projections. **waiting on evidence** for Dock/2ndMenuBar surfaces because each needs real AppKit lifecycle, accessibility, and multi-display verification. Keep all state local and do not add another session scanner. |
| [seanhellwig/sidepulse](https://github.com/seanhellwig/sidepulse) ([compare](https://github.com/inteliwear/sidepulse/compare/main...seanhellwig:main)) | Seven commits ahead and 13 behind the upstream snapshot. [OpenCode provider support](https://github.com/seanhellwig/sidepulse/commit/9d759dd619aa8784f05a04cf9b7dd53c1c95bd94) adds a plugin and provider path; [terminal selection](https://github.com/seanhellwig/sidepulse/commit/f3bb50a1185c3bed6263fe9f809001a64a209fd5) adds Ghostty/Apple Events entitlement; later commits document XDG-aware plugin paths and add an unsigned package wrapper ([commit](https://github.com/seanhellwig/sidepulse/commit/36c324c8b8c41256360e04f86ef75b1777b69c37)). | **adopted/surpassed** for the provider-adapter idea: JR Bar's OpenCode installer is marker-owned, idempotent, bounded, and tested. **reject** the unsigned package as a release answer and do not broaden Apple Events merely for convenience; signing, notarization, least privilege, and explicit terminal consent remain release gates. |

## CodexBar, current release `v0.56.1`

[CodexBar 0.56.1](https://github.com/steipete/CodexBar/releases/tag/v0.56.1) is the current stable release at this refresh. Its release notes include:

- privacy-safe hiding of personal project/source names and paths while retaining
  cost and token totals;
- one-pass Codex cost loading, preservation of last-known-good values, explicit
  unknown/unpriced history, and redacted weekly-reset diagnostics;
- Keychain migration gating and OpenCode-backed OAuth quota documentation.

The [README privacy note](https://github.com/steipete/CodexBar#privacy-note) says
the app reads bounded known locations only when related features are enabled,
asks before process/session inspection, and discards paths and identities when
session detail is hidden. Those are useful **adapt** leads: JR Bar should keep
source/path redaction, last-known-good versus zero distinction, and
content-free diagnostics as explicit tests. The fork already has local
provider-credential ownership, freshness/error states, private fixture rules,
and bounded usage stores, so importing CodexBar's broad provider model is
**adopted/surpassed** only at the discipline level, not by copying its
provider-specific fields.

Do not copy CodexBar's browser-cookie breadth or its many provider adapters into
the status-light path. Browser cookies, Full Disk Access, and undocumented web
RPCs are **waiting on evidence** for any particular JR Bar provider and remain
opt-in, read-only capacity work. Never make them prerequisites for the free
local agent experience. Credentials stay with the owning CLI or provider,
and no transcript or token belongs in the LED or default diagnostic payload.

## T3 Code, current release `v0.0.36`

[T3 Code v0.0.36](https://github.com/pingdotgg/t3code/releases/tag/v0.0.36) is the
current stable release at this refresh. Its release notes show several useful
engineering signals:

- replay all unapplied events during projection bootstrap;
- recover stale Codex approval callbacks;
- improve OpenCode child approvals, stops, and model catalogs;
- keep hidden previews from draining battery;
- make thread auto-settling opt-in, and split provider settings into list and
  editor views.

These are **adapt** leads, not a reason to import T3's controller. JR Bar can
continue its own typed receipts, stale/late callback rejection, bounded worker
drains, provider-instance identity, and power-aware finite cues. The specific
release behaviors need source-level and installed-app evidence before being
called complete in JR Bar. T3's [README](https://github.com/pingdotgg/t3code)
also describes a web, mobile, and Electron control surface and says the server
runs on the user's machine. That makes its provider-instance and projection
patterns useful, while the broad remote-ready surface remains **waiting on
evidence** for JR Bar's authenticated, read-only, content-minimized remote
observation.

The release also advertises analytics for connected client platforms and large
file uploads. Those are **reject** for JR Bar's ambient local-first product:
analytics would add an external data-flow gate with no light-signal value, and
uploads would expand the attack and privacy surface beyond observation. A
future owner-approved diagnostic export can remain local and bounded, but it
must not become telemetry by default.

## P5.72 action queue

1. Keep the already-adopted #37 socket candidate probing and #32 ordered FIFO
   contracts under regression coverage.
2. Profile real SidePulse Pro writes before deciding whether #33's one-second
   trailing-edge floor improves durability without delaying urgent cues.
3. Reproduce issue #25 and issue #23 on physical hardware, with readback and
   sleep/wake receipts, before changing battery arbitration or USB recovery.
4. Treat the fixed hybrid layout and HypeLaser's secondary surfaces as
   evidence-gated experiments. Do not add arbitrary segment definitions or a
   second scanner.
5. Keep remote observation opt-in, authenticated, bounded, read-only, and
   content-free by default. Do not adopt upstream remote host management or
   T3 analytics as a shortcut around those gates.

This refresh does not authorize a merge, push, release, deployment, credential
change, telemetry, or hardware mutation.
