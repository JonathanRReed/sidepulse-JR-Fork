# SidePulse Manager Task Contract

## Objective and Completion Boundary

- Terminal objective: Make SidePulse a trustworthy all-agent and hardware manager by implementing the approved attention, failure, completion, freshness, usage, notification, performance, packaging, and local-data hardening design.
- Current completion claim: Implementation authorized, no implementation task completed yet.
- Observable result for the active tranche: A non-actionable failure plays exactly two repetitions on every enabled SidePulse surface and then returns to the current lifecycle state, while only live actionable approval or input waits persist.

## Current State

- Active product and working directory: SidePulse in `/Users/jonathanreed/Documents/Codex/2026-08-12/hey-so-i-was-working-on/work/sidepulse-manager-completion`.
- Repository, branch, remote, revision, or artifact: branch `codex/sidepulse-manager-completion`, based on `12e92a088648480331f25d2f1cccaede9828a488`.
- Dirty-work ownership or unrelated changes: clean isolated worktree at contract creation; all later changes in this worktree belong to this task.
- Running process, listener, or open application: installed SidePulse app from `/Users/jonathanreed/Applications/SidePulse.app`, initially running the pre-change build.
- User-visible failure or desired endpoint: stopped token-limited workers leave Screen Bar or hardware in a persistent Ask pulse even though no user action is possible, and the Screen Bar animation is visibly laggy.
- Last verified checkpoint: `ruff check src tests` passed and `PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/ -q` reported 591 passed.

## Priority Scope

1. Lifecycle truth, shared attention projection, finite failure signaling, and completion correctness.
2. Freshness, usage/menu refresh, modern notification behavior, and always-on performance.
3. Signed-bundle runtime integrity, trusted command resolution, owner-only local data, bounded retention, and live hardware/package verification.

## Deferred, External, and Out of Scope

- Deferred: no approved requirement is deferred; work proceeds one verified tranche at a time.
- External blocker: physical hardware verification requires a mounted writable SidePulse device; notification authorization may require a user-visible macOS permission action.
- Out of scope: redesigning every signal's visual identity, publishing a
  release, pushing a branch, deploying anything, browser-cookie usage scraping,
  private provider endpoints, and arbitrary executable plugins. The user has
  approved installed-agent registry, Google coding surfaces, provider-specific
  cloud capacity, and the selected community adaptations as new source tranches.

## Evidence Available

- Direct receipts: exact source revision, clean baseline, 591 passing tests, live false-Ask reproduction, short CPU and stack sample, and completed security scan.
- Prior claims or stale evidence: earlier Claude/Fable implementation summaries and screen observations.
- Inferences: T3 Code state separation and CodexBar policy separation are suitable architectural references.
- Unknowns: post-change physical-device behavior, long-duration energy use, installed package integrity, and current macOS notification authorization.

## Acceptance Gates

| Gate | Required now | Required evidence | Status | Direct receipt |
|---|---|---|---|---|
| Functionality | yes | red-green tests plus live reproduction | Baseline only | 591 baseline tests passed |
| UX and accessibility | yes | rendered menu and Screen Bar inspection for normal, failure, Ask, completion, stale, and error states | Not run | none |
| Runtime and performance | yes | fixed 60-second CPU scenarios and 30-minute cache or memory scenario | Baseline sampled | 10.8 to 18.1 percent short CPU sample |
| Data and provenance | yes | freshness, dedupe, bounded cache, and complete delivery-batch tests | Not run | none |
| Security and privacy | yes | focused security tests and rescan | Failing baseline | scan found one high and one medium issue |
| Packaging and trust | yes | rebuilt bundle import-root, signature, command-path, and launch checks | Failing baseline | mutable external import path observed |
| Deployment and release | no | not authorized | Out of scope | none |

## Authority

- Edits: approved within the isolated SidePulse worktree and local installed test application.
- Dependency additions: not approved; ask before adding any production dependency.
- Deletion or destructive action: not approved beyond safe replacement through existing build/install flows; preserve recoverable backups where relevant.
- Commit, push, or pull request: not approved.
- Deployment or production changes: not approved.
- Spending or billing: not approved.
- Credential or permission changes: do not change credentials; a user-visible OS permission prompt is an external acceptance step, not silent authorization.

## Active Bounded Vertical Tranche

- Scope: shared semantic projection and two-repetition failure signal across menu, Screen Bar, physical hardware, pinned devices, click target, and escalation.
- Observable state change: stopped or terminal failures never latch Ask; a new failure plays twice and restores the live lifecycle display without another agent event.
- Checks and receipts required before closure: targeted red-green tests, full Ruff and pytest, rebuilt installed app, timed Screen Bar observation, physical device observation if mounted, and state/menu agreement.
- One next tranche if the terminal objective remains unmet: completion delivery independence and same-poll batch preservation.

## Instruction Changes

| New instruction | Replace, Add, Defer, or Reprioritize | Contract effect |
|---|---|---|
| “Do it all and make this the single best all-agent and harness manager” | Add and reprioritize | Retains the approved failure design, expands execution through all audited product, hardware, efficiency, and hardening gates, and prioritizes lifecycle truth first. |
| “The screen bar at the top, the animation is pretty laggy” | Add and reprioritize | Records lag as a current rendered defect and moves adaptive rendering and draw reduction immediately after the attention/failure tranche. |
| “Fully approved” for the installed-agent, capacity, and community design | Add | Authorizes source design and implementation of the installed-surface registry, Google coding surfaces, exact provider capacity, and bounded community adaptations. It does not authorize commits, pushes, dependencies, installs, credentials, permissions, or deployment. |
| “Do we currently have OpenCode as a provider? If we don’t, we should definitely add them.” | Add and reprioritize | OpenCode becomes the first installed-agent registry tranche. It receives canonical local lifecycle and actionable-request authority through its official plugin events, while capacity remains delegated to its configured upstream provider. |

## Stop Rule

Do not begin another tranche until the active tranche's required gates are observed, explicitly failed, or named as blocked, and its direct receipts are recorded.
