# SidePulse Capacity Pace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add conservative reset-aware pace and runout forecasting to Capacity, with provider-cycle warning dedupe and no fabricated predictions.

**Architecture:** A pure pace module consumes normalized usage windows and optional provider-owned historical samples. The AppKit card renders the result and the existing transition system publishes one bounded warning per proven reset cycle.

**Tech Stack:** Python 3.10+, frozen dataclasses, existing usage/reset models, AppKit/PyObjC, pytest, Ruff.

## Global Constraints

- Linear pace requires known usage, future reset, and explicit positive duration.
- Suppress pace until at least 3 percent of the window has elapsed.
- Never reuse pace history across provider, account discriminator, semantic duration, or reset cycle.
- A warning may fire at most once per provider, identity, semantic window, and reset cycle until recovery.
- Remaining headroom is primary. Used percentage remains available in Settings and history.
- Do not add dependencies, access provider credentials or real user history, install or restart the app, or touch mounted hardware during source tasks.
- Do not commit unless the user separately authorizes commits.

---

### Task 1: Pure linear pace

**Files:**
- Create: `src/sidepulse/capacity_pace.py`
- Create: `tests/test_capacity_pace.py`
- Modify: `src/sidepulse/usage_view.py`
- Modify: `tests/test_usage_view.py`

**Interfaces:**

```python
class CapacityPaceStage(str, Enum):
    ON_TRACK = "on-track"
    RESERVE = "reserve"
    FAR_RESERVE = "far-reserve"
    DEFICIT = "deficit"
    FAR_DEFICIT = "far-deficit"

@dataclass(frozen=True, slots=True)
class CapacityPace:
    stage: CapacityPaceStage
    expected_used_percent: float
    actual_used_percent: float
    delta_percent: float
    remaining_percent: float
    eta_seconds: float | None
    will_last_to_reset: bool
    confidence: str

def project_linear_capacity_pace(
    window: UsageWindowViewModel,
    *,
    now: float,
) -> CapacityPace | None: ...
```

- [ ] **Step 1: Write literal pace tables**

Cover missing duration, reset-only, missing reset, expired reset, reset beyond duration, exact start with nonzero use, under-3-percent elapsed suppression, 0 percent use, exhausted use, on-track, ahead, far-ahead, behind, runout ETA before reset, and sustainable use through reset.

- [ ] **Step 2: Capture missing-module RED**

```sh
PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_capacity_pace.py tests/test_usage_view.py -q
```

- [ ] **Step 3: Implement minimal pure math**

Compute window start as `reset_epoch - window_minutes * 60`. Reject resets whose remaining time exceeds duration. Expected use is elapsed divided by duration. ETA uses current average burn only when elapsed and actual are positive. Stage thresholds are literal test-owned constants, not provider names.

- [ ] **Step 4: Add pure presentation**

Expose compact text such as `on pace`, `12% in reserve`, `8% in deficit`, or `may run out in 2h` only when a pace exists. Keep reset and staleness text separate.

- [ ] **Step 5: Verify and report**

Run focused pytest, Ruff, diff check, and write `.superpowers/sdd/2026-08-12-sidepulse-operator-experience/task-4-report.md`.

### Task 2: Historical pace ownership

**Files:**
- Create: `src/sidepulse/capacity_history.py`
- Create: `tests/test_capacity_history.py`
- Modify: `src/sidepulse/capacity_pace.py`
- Modify: `tests/test_capacity_pace.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class CapacitySample:
    provider_id: str
    account_key: str | None
    semantic_key: str
    window_minutes: int
    captured_at: float
    used_percent: float
    reset_epoch: float

def project_historical_capacity_pace(
    window: UsageWindowViewModel,
    samples,
    *,
    account_key: str | None,
    now: float,
    minimum_samples: int = 4,
) -> CapacityPace | None: ...
```

- [ ] **Step 1: Write ownership RED cases**

Prove provider, account, semantic duration, chronological order, reset equivalence, minimum sample count, partial samples, outliers, and stale cycles. Explicitly prove another account's healthier history cannot affect the current account.

- [ ] **Step 2: Capture RED, implement bounded median calibration, and verify GREEN**

Use at most the newest 64 valid samples. Derive the expected curve only from matching cycles. Fall back to linear pace when historical evidence is absent or invalid.

- [ ] **Step 3: Verify cache and privacy behavior**

Historical records contain numeric capacity facts and opaque provider/account discriminators only. Account display names and emails are not persisted.

### Task 3: Capacity card and warning integration

**Files:**
- Modify: `src/sidepulse/status_bar.py`
- Modify: `src/sidepulse/signals.py`
- Modify: `src/sidepulse/settings.py`
- Modify: `src/sidepulse/settings_window.py`
- Modify: `tests/test_sidepulse.py`
- Create: `tests/test_capacity_warning.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class CapacityWarningKey:
    provider_id: str
    account_key: str
    semantic_key: str
    window_minutes: int | None
    reset_epoch: float

def should_emit_capacity_warning(...): ...
```

- [ ] **Step 1: Write production RED tests**

Prove in-place card mutation, remaining plus pace text, no pace for insufficient evidence, one warning per cycle, small reset corrections retaining the warned cycle, new cycle rearming, recovery clearing the key, provider/account isolation, settings opt-out, quiet-hours hold, no permanent blinking, and 64-key bound.

- [ ] **Step 2: Capture RED**

Run:

```sh
PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_capacity_pace.py tests/test_capacity_history.py tests/test_capacity_warning.py tests/test_sidepulse.py -q -k 'capacity or pace or warning or usage'
```

- [ ] **Step 3: Implement in the existing pipeline**

Do not add a fetcher or thread. Compute pace after accepted provider results, update stable labels, and route the warning through the existing finite signal coordinator. Do not add modern UserNotifications until its production dependency is explicitly approved.

- [ ] **Step 4: Run focused, neighboring, and full static gates**

Run full Ruff, the Step 2 pytest command, usage/reset/refresh neighbors, and diff check.

- [ ] **Step 5: Independently review Task 3**

Review clock domains, false forecast, cross-account reuse, reset correction duplicate warnings, first-observation noise, history poisoning, accessibility copy, and signal termination.

### Task 4: Installed Capacity acceptance

**Files:**
- Create: `.superpowers/sdd/2026-08-12-sidepulse-operator-experience/task-7-report.md`

- [ ] **Step 1: Build a private candidate and verify package identity**
- [ ] **Step 2: Render deterministic on-track, at-risk, stale, partial, empty, and error fixtures**
- [ ] **Step 3: Observe reset rollover and warning termination without using real provider credentials**
- [ ] **Step 4: Record source, package, rendered, installed, and external-dependency boundaries separately**
