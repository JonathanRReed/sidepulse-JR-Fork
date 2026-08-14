# SidePulse Next Improvements Research Ledger

## Decision Contract

- Decision: choose the next high-value improvement tranches for SidePulse after Presentation Task 8 integration, prioritizing Screen Bar smoothness, semantic parity, operator clarity, and source-grounded safety.
- Audience: Jonathan Reed and the active SidePulse implementation/review team.
- Geography: local macOS product behavior, no hosted service decision in scope.
- Timeframe: current shared tree as of 2026-08-13.
- As-of date: 2026-08-13.
- Evaluation criteria:
  - Improves visible product behavior or operator decision quality.
  - Preserves canonical source boundaries and privacy constraints.
  - Fits the current plan sequencing or identifies a better sequence with evidence.
  - Has source or test evidence in SidePulse and local reference repos.

## Source Budget

- Maximum unique substantive sources: 10.
- Planned allocation:
  - 4 SidePulse plan and implementation sources.
  - 3 CodexBar reference sources.
  - 2 T3 reference sources.
  - 1 challenge pass for contradictory or already-covered ideas.

## Decision Threshold

- Sufficient evidence requires:
  - Coverage of all decision-critical claims with at least one direct SidePulse source and one corroborating implementation or reference source.
  - Clear sequencing guidance for the next 3-5 tranches.
  - Explicit rejection of ideas already completed or blocked by current architecture.

## Claim Ledger

| id | importance | claim | status | evidence | notes |
| --- | --- | --- | --- | --- | --- |
| C1 | decision-critical | Presentation Task 11 shared physical/virtual resolver is the highest-value next tranche once Task 8 is independently cleared. | investigating | pending | Core user ask is semantic parity and full-line relay consistency. |
| C2 | decision-critical | SidePulse still lacks some operator-facing remaining/reset/history affordances that CodexBar already proves valuable. | investigating | pending | Must distinguish pure foundation work from integrated UI. |
| C3 | decision-critical | Some improvements in CodexBar are not relevant because SidePulse is a semantic status product, not a provider quota dashboard. | investigating | pending | Need a challenge pass to avoid importing the wrong product model. |
| C4 | supporting | Accessibility and menu stability should be integrated after parity and core capacity UI, not before. | investigating | pending | Depends on controller serialization and live value. |
| C5 | supporting | Current local references reveal additional test gates for notification churn, reset alignment, and in-flight stability that SidePulse can adapt. | investigating | pending | Only if they map to SidePulse semantics without duplicating solved work. |

## Source Ledger

| id | type | title | locator | status | notes |
| --- | --- | --- | --- | --- | --- |
| S1 | primary | Wave 2 presentation plan | `.superpowers/sdd/2026-08-12-sidepulse-operator-experience/wave-2-implementation-presentation.md` | active | Task sequencing and acceptance contract. |
| S2 | primary | Shared tree status | `git status --short` in `work/sidepulse-manager-completion` | active | Confirms dirty tree and broad concurrent work. |
| S3 | primary | Presentation Task 8 report | `.superpowers/sdd/2026-08-12-sidepulse-operator-experience/wave-2-presentation-task-8-report.md` | active | Confirms exact implementation/evidence boundaries. |
| S4 | primary | Capacity and operator reports/status | active agent outputs and task reports | active | Needed for sequencing non-presentation follow-ons. |
| S5 | corroborating | CodexBar README | `work/reference-sources/CodexBar/README.md` | active | High-level feature model and user value. |
| S6 | corroborating | CodexBar CHANGELOG | `work/reference-sources/CodexBar/CHANGELOG.md` | active | Dense evidence of solved UX and data-quality issues. |
| S7 | corroborating | T3 reference repo | `work/reference-sources/t3code` | active | Need targeted searches, not broad import. |

## Stop State

- Current state: in progress.
- Stop condition: threshold met when next-tranche ordering and concrete improvement list are evidence-backed and non-duplicative.
