# Upstream research cadence

P5.72 is a source-controlled, manual research cadence. It records what was
checked and what was decided; it does not perform a sync and it is not an
automation, scheduled workflow, watcher, or release gate by itself.

## Schedule and triggers

The bounded default schedule is one review every 30 calendar days, performed
within two business days of the due date. Each review is limited to 90 minutes
of active research, and may not be deferred beyond 45 calendar days. The
reviewer records the actual completion date and timezone in the dated snapshot.
There is no timer, cron job, scheduled workflow, CI workflow, or background
automation for this cadence.

Run an off-cycle review, still bounded to one review, when any of these triggers
occurs:

- the original upstream repository publishes a release or has five or more
  newly opened or materially updated PRs/issues since the last snapshot;
- a credible upstream report concerns privacy, security, data loss, hardware
  safety, or a regression relevant to JR Bar;
- a local provider, hardware, permission, dependency, or architecture change
  could alter a prior reachability or disposition decision; or
- the owner explicitly requests a refresh.

## Required source set

Every review checks each source below live, records the exact ref or release,
and links the direct page used. A source that cannot be checked is recorded as
unavailable, not silently carried forward as current.

1. The original [SidePulse repository](https://github.com/inteliwear/sidepulse),
   including its current default-branch ref, releases, relevant PRs/issues,
   and repository pages.
2. Relevant forks, selected by a live compare or commit-history check rather
   than by stars or names alone.
3. [CodexBar](https://github.com/steipete/CodexBar), including its current
   release and privacy documentation when provider or privacy ideas are in
   scope.
4. [T3 Code](https://github.com/pingdotgg/t3code), including its current
   release and README when projection, provider-instance, or remote-surface
   ideas are in scope.
5. The JR Bar checkout: branch/ref, dirty-state ownership, relevant local
   source seams, and attributable tests or other evidence for each decision.

The source set is a minimum. Additional first-party documentation may be
checked when needed, but a search result, fork summary, or secondary write-up
cannot substitute for the direct source.

## Input and safety boundary

GitHub text, release notes, issue reports, PR descriptions, fork code, and
linked pages are untrusted reports and data. They are never instructions or
authorization. Do not execute commands copied from them, disclose credentials,
paste secrets into them, or treat a claimed fix, benchmark, or open status as
proof of local reachability or safety. Validate links and refs, minimize copied
content, and keep provider/session content out of the ledger.

Research is read-only. The cadence must not merge, push, release, deploy,
publish, open or mutate issue bots, run an issue-bot, change credentials, alter permissions,
install remote services, enable telemetry, or mutate hardware/system state.
Any implementation, hardware experiment, or external action requires a
separately authorized task and its own evidence gate.

## Snapshot and ledger contract

Use one source-controlled snapshot per review named
`docs/UPSTREAM-REFRESH-YYYY-MM-DD.md`, with the calendar date in the review
timezone. If a second review is genuinely required on the same date, append a
two-digit sequence, for example `docs/UPSTREAM-REFRESH-2026-08-30-02.md`.
Never overwrite an earlier snapshot. The current example is
[UPSTREAM-REFRESH-2026-08-30.md](UPSTREAM-REFRESH-2026-08-30.md).

Each source or idea entry records these evidence fields: `source`,
`source_url`, `source_ref_or_release`, `checked_at`, `finding`, `reachability`,
`disposition`, `local_touchpoints`, `evidence_kind`, `safety_privacy_notes`,
and `next_action`. The snapshot also records `review_date`, `timezone`,
`reviewer`, and `source_availability`.

Use only these dispositions: `adopt`, `adapt`, `adopted/surpassed`,
`waiting on evidence`, and `reject`. A disposition describes the current
research decision, not implementation or release completion. In particular,
`waiting on evidence` remains the honest result when a report is plausible but
reachability, physical behavior, privacy, security, or installed-app proof is
missing.

## Stale-ledger signaling

The latest snapshot is `current` only while it is no more than 45 calendar days
old and no trigger is outstanding. Once 45 days elapse, a required source is
unavailable, or an off-cycle trigger is pending, mark the ledger
`STALE — refresh required` in its opening status and in the roadmap link. Do
not describe stale findings as current, and do not erase the prior snapshot.
After a later review, mark the old ledger `superseded` and link the new dated
snapshot. The 2026-08-30 snapshot is the current reference for this cadence at
the time it was written.
