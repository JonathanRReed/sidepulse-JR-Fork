# Upstream Sync Review

Reviewed on August 15, 2026 against the ten upstream commits added after merge base `f4dd7e0`. They are not merged wholesale because the JR fork is 160 commits ahead and its controller, installer, packaging, and session-navigation design have intentionally diverged.

| Upstream work | JR-fork disposition |
| --- | --- |
| Custom terminal selection and terminal-window reuse | Already superseded by `status_bar_launch.py`, `navigation_policy.py`, and `session_actions.py`, including Terminal, iTerm2, and verified direct Ghostty execution. |
| Missing ScriptingBridge dependency | Not applicable. This fork has no ScriptingBridge import. Adding the framework would add install weight without repairing reachable behavior. |
| Isolated user installer | Ported as `scripts/install-user.sh` for CLI users while preserving the fork's sealed app-bundle, signed-package, and launch-agent paths. |
| Packaging and clean-install tests | Adopted through package-contract tests and `scripts/verify_clean_install.py`. |
| Hook stability and compatibility | Adopted through fail-open current and legacy hook-module entrypoints. |
| PR #31 display-sleep-safe keep-awake | Adapted as an explicit owner choice. JR-Bar defaults both ordinary and closed-lid `caffeinate` commands to no display assertion, adds `d` only when selected, and keeps battery and privileged closed-lid policy independent. |
| PR #32 hook-latency evidence | Adapted as a standard-library admission client plus one app-owned bounded FIFO. JR-Bar keeps ordering, tracked provider children, explicit overload receipts, and shutdown drain instead of adopting the PR's detached, potentially reordered process shape. |
| Version and post-build guards | Adopted through `scripts/validate_release_version.py`, Twine validation, clean-wheel installation, and checksum generation. |
| Status-bar UI patches | Not copied by file name. Upstream's controller is substantially different; any behavior must be ported with a fork-native regression test. |

Future upstream syncs should be reviewed commit by commit. Prefer porting a behavior and its test over merging a large controller diff.

PRs #31 and #32 were additionally reviewed on August 29, 2026. PR #31 reported the
`caffeinate -d` display assertion keeping the display on and preventing normal
locking. JR-Bar ports that behavior through its own power-policy and settings
architecture rather than copying the upstream controller patch.

The current-run local-health panel added on August 29, 2026 is fork-native. It
reuses JR-Bar's existing presentation, worker, and performance snapshots and
does not add upstream telemetry, cloud reporting, persistence, or a parallel
metrics framework.

The deeper Why this light context is also fork-native. It retains JR-Bar's
existing `DecisionTrace` semantic ladder, then adds one immutable projection of
cached policy, suppression, surface, accessibility, and timing facts. It does
not copy controller state, retain content-bearing identifiers, probe providers
or devices, persist explanation history, or add telemetry. Selection and scroll
position are preserved inside the existing selectable panel instead of
introducing a second diagnostics window.

## P5.72 research cadence

Recurring upstream review is source-controlled and manual. The exact schedule,
live source set, untrusted-input boundary, dated snapshot naming, disposition
vocabulary, evidence fields, no-mutation rules, and stale-ledger signaling are
defined in [UPSTREAM-RESEARCH-CADENCE.md](UPSTREAM-RESEARCH-CADENCE.md).
The first linked snapshot is the [2026-08-30 refresh](UPSTREAM-REFRESH-2026-08-30.md).
This is a review record, not authorization to merge, push, release, deploy,
change credentials, run an issue bot, or mutate hardware/system state.
