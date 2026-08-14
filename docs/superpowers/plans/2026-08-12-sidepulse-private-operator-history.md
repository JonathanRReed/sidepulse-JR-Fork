# SidePulse Private Operator History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a private, bounded, prompt-free history of agent outcomes, attention episodes, usage, and capacity samples with a quiet native Settings presentation.

**Architecture:** A pure daily aggregator converts authoritative lifecycle transitions and existing usage totals into derived records. A private atomic store retains bounded days, and a stable Settings view renders summaries and a quiet semantic timeline.

**Tech Stack:** Python 3.10+, frozen dataclasses, JSON, existing private I/O, AppKit/PyObjC, pytest, Ruff.

## Global Constraints

- Persist only derived counts, durations, numeric usage, coverage, and capacity samples.
- Never persist agent or session titles, prompts, messages, commands, tool arguments, paths, raw errors, user names, account emails, or credentials.
- Default retention is 30 days. Supported choices are 7, 30, and 90 days.
- One provider or source failure cannot erase valid sibling facts.
- History is descriptive and never produces a productivity score.
- Do not add dependencies, inspect real user history, install or restart the app, or touch mounted hardware during source tasks.
- Do not commit unless the user separately authorizes commits.

---

### Task 1: Pure derived history model

**Files:**
- Create: `src/sidepulse/operator_history.py`
- Create: `tests/test_operator_history.py`

**Interfaces:**

```python
class OperatorEventKind(str, Enum):
    AGENT_STARTED = "agent-started"
    NEEDS_USER = "needs-user"
    COMPLETED = "completed"
    FAILED = "failed"
    CAPACITY_RISK = "capacity-risk"
    CAPACITY_RECOVERED = "capacity-recovered"
    DEVICE_LOST = "device-lost"
    DEVICE_RECOVERED = "device-recovered"
    COVERAGE_DEGRADED = "coverage-degraded"
    COVERAGE_RECOVERED = "coverage-recovered"

@dataclass(frozen=True, slots=True)
class OperatorHistoryEvent:
    provider_id: str
    identity_key: str
    kind: OperatorEventKind
    occurred_at: float
    active_seconds: float = 0.0

@dataclass(frozen=True, slots=True)
class OperatorHistoryDay:
    day_key: str
    timezone_offset_minutes: int
    provider_id: str
    started: int
    completed: int
    failed: int
    attention_episodes: int
    active_seconds: float
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float
    coverage_status: str
    sample_count: int
    capacity_samples: tuple[CapacitySample, ...]
```

- [ ] **Step 1: Write privacy-first RED tests**

Prove identity dedupe, transition-only counting, duplicate/out-of-order input, active-time capping, provider isolation, local-day boundaries, DST offset changes, partial sibling preservation, capacity sample bounds, and rejection of nonfinite values.

- [ ] **Step 2: Capture RED**

```sh
PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_operator_history.py -q
```

- [ ] **Step 3: Implement pure aggregation**

Count lifecycle edges, not polls. Cap an individual active interval at 24 hours and a day at 24 hours per identity. Store opaque identity only in the transient event input; daily output contains no identity.

- [ ] **Step 4: Mutation-test forbidden text**

Feed prompts, commands, paths, secret-like values, and raw errors into source fixtures and assert they cannot appear anywhere in `repr`, `asdict`, or serialized daily output.

### Task 2: Private bounded history store

**Files:**
- Create: `src/sidepulse/operator_history_store.py`
- Create: `tests/test_operator_history_store.py`
- Modify: `src/sidepulse/settings.py`

**Interfaces:**

```python
class OperatorHistoryStore:
    def load(self) -> tuple[OperatorHistoryDay, ...]: ...
    def merge(self, days) -> tuple[OperatorHistoryDay, ...]: ...
    def clear(self) -> None: ...
```

- [ ] **Step 1: Write real-filesystem RED tests**

Use temporary roots. Prove 0700 directory, 0600 file, atomic write, replace failure retaining dirty state, corrupt recovery, symlink/hard-link/parent-swap refusal through private I/O, deterministic retention, same-day merge, file-size bound, clear targeting, and concurrency dirtiness.

- [ ] **Step 2: Capture RED, implement, and verify GREEN**

Use existing private I/O helpers only. Do not introduce a database or new dependency. Serialize schema version plus days. Retain only the newest configured 7, 30, or 90 day keys and cap capacity samples per provider-day to 96.

- [ ] **Step 3: Run private security neighbors**

Run operator store, private I/O, freshness, settings, Ruff, and diff checks.

### Task 3: Controller ingestion and quiet projection

**Files:**
- Create: `src/sidepulse/operator_timeline.py`
- Create: `tests/test_operator_timeline.py`
- Modify: `src/sidepulse/status_bar.py`
- Modify: `tests/test_sidepulse.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class OperatorTimelineRow:
    occurred_at: float
    provider_id: str
    kind: OperatorEventKind
    label: str

def project_operator_timeline(events, *, limit: int = 50) -> tuple[OperatorTimelineRow, ...]: ...
```

- [ ] **Step 1: Write controller RED tests**

Prove poll dedupe, lifecycle edge capture, worker collapse by parent identity, terminal preservation, attention edge dedupe, device health edge dedupe, capacity/coverage transitions, accepted-provider-generation fencing, no history work on every Screen Bar frame, and flush on termination.

- [ ] **Step 2: Capture RED**

Run:

```sh
PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_operator_history.py tests/test_operator_history_store.py tests/test_operator_timeline.py tests/test_sidepulse.py -q -k 'operator_history or timeline or completion or failure or usage or device'
```

- [ ] **Step 3: Integrate at authoritative boundaries**

Ingest lifecycle transitions after canonical projection, usage after accepted generations, and device health after deduped write outcomes. Debounce persistence and flush on termination. Timeline labels come from a fixed product vocabulary.

- [ ] **Step 4: Run focused and neighboring gates**

Run full Ruff, the Step 2 selection, private I/O and freshness neighbors, and diff check.

### Task 4: Native history surface and clear control

**Files:**
- Modify: `src/sidepulse/settings_window.py`
- Modify: `src/sidepulse/status_bar.py`
- Modify: `tests/test_sidepulse.py`

- [ ] **Step 1: Write rendered AppKit RED tests**

Prove today, 7-day, and 30-day totals; provider-local partial labels; a quiet bounded timeline; no raw text; accessibility; range control; retention control; clear confirmation; clear failure; empty state; and stable view mutation.

- [ ] **Step 2: Capture RED and implement the smallest Settings pane**

Reuse project-native cards, spacing, fonts, and existing graph primitives. Do not create a large dashboard window, gradients, leaderboards, scores, or decorative KPI tiles.

- [ ] **Step 3: Run rendered source gates**

Run focused Settings/AppKit tests, Ruff, diff check, and screenshot a deterministic fixture for visual review.

- [ ] **Step 4: Independently review history privacy and usability**

Review serialization, logs, UI strings, accessibility, retention, clear targeting, partial truth, high-volume rendering, and absence of user scoring.

### Task 5: Candidate and installed history acceptance

**Files:**
- Create: `.superpowers/sdd/2026-08-12-sidepulse-operator-experience/task-12-report.md`

- [ ] **Step 1: Run guarded full suite and bundle verification**
- [ ] **Step 2: Launch a private candidate with synthetic history only**
- [ ] **Step 3: Verify visual ranges, VoiceOver, clear, corrupt recovery, and persistence across candidate restart**
- [ ] **Step 4: Record source, package, candidate, installed, and user-data boundaries separately**
