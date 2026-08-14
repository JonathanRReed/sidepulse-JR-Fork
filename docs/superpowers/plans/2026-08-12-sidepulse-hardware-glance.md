# SidePulse Hardware Glance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Screen Bar and physical devices share one automatic glance policy for attention, active work, finite transitions, capacity horizon, and rest.

**Architecture:** A pure resolver selects one semantic glance layer, and a pure capacity-horizon renderer maps normalized remaining headroom across arbitrary LED counts. Existing physical and virtual transports consume the same resolved program and elapsed phase.

**Tech Stack:** Python 3.10+, existing LED DSL/WASM, AppKit/PyObjC Screen Bar, pytest, Ruff.

## Global Constraints

- Priority is actionable attention, active work, fresh finite failure/completion, credible capacity risk, then resting glow.
- Finite signals repeat at most twice and never blink indefinitely.
- Active work and capacity horizon use the full available line for 2, 8, and arbitrary tested LED counts.
- Physical and virtual surfaces share normalized semantics and elapsed phase.
- Reduced Motion uses static state-preserving output.
- Device write failures remain visible and are never recorded as successful.
- Do not add dependencies, install or restart the app, or touch mounted hardware during source tasks.
- Do not commit unless the user separately authorizes commits.

---

### Task 1: Pure automatic glance resolver

**Files:**
- Create: `src/sidepulse/glance_policy.py`
- Create: `tests/test_glance_policy.py`

**Interfaces:**

```python
class GlanceKind(str, Enum):
    ATTENTION = "attention"
    ACTIVE = "active"
    FAILURE = "failure"
    COMPLETION = "completion"
    CAPACITY = "capacity"
    REST = "rest"

@dataclass(frozen=True, slots=True)
class GlanceProjection:
    kind: GlanceKind
    provider_id: str | None
    remaining_percent: float | None
    episode_key: str | None

def project_glance(
    attention,
    *,
    has_active_work: bool,
    transition_signals,
    capacity_models,
    capacity_enabled: bool,
) -> GlanceProjection: ...
```

- [ ] **Step 1: Write priority and truth-table RED tests**

Prove every pairwise priority, stale/future signal rejection, transition expiry, capacity credibility, constrained-provider selection, provider-order tie break, and rest fallback.

- [ ] **Step 2: Capture RED, implement pure projection, and verify GREEN**

Use authoritative attention and accepted Capacity models only. Select capacity by lowest credible remaining percentage. Do not reclassify raw status payloads.

### Task 2: Pure full-line capacity horizon

**Files:**
- Create: `src/sidepulse/capacity_horizon.py`
- Create: `tests/test_capacity_horizon.py`
- Modify: `src/sidepulse/colors.py`

**Interfaces:**

```python
def capacity_horizon_program(
    *,
    led_count: int,
    remaining_percent: float,
    provider_color: str,
    brightness: float,
    at_risk: bool,
    reduced_motion: bool,
) -> str: ...
```

- [ ] **Step 1: Write arbitrary-count RED tables**

Cover LED counts 1, 2, 3, 5, 8, and 16; remaining 0, 1, 25, 50, 99, and 100; clamping; invalid counts; provider color; brightness floor; risk boundary motion; reduced motion; and byte stability.

- [ ] **Step 2: Capture RED and implement using the existing LED DSL**

Filled positions use provider color. One boundary position may breathe only when at risk and reduced motion is off. Empty positions use the calibrated rest floor. Avoid a two-position loop and ensure monotonic full-line fill.

- [ ] **Step 3: Verify the generated programs through the existing WASM parser**

Run every table through `sdled.wasm`, then sample phase output for 2 and 8 LEDs. Assert every program parses and returns the expected number of colors.

### Task 3: Physical and Screen Bar integration

**Files:**
- Modify: `src/sidepulse/status_bar.py`
- Modify: `src/sidepulse/led_status.py`
- Modify: `src/sidepulse/virtual_device.py`
- Modify: `src/sidepulse/settings.py`
- Modify: `src/sidepulse/settings_window.py`
- Modify: `tests/test_sidepulse.py`

- [ ] **Step 1: Write production RED tests**

Prove same glance selection on physical and virtual surfaces, same normalized phase, deduped physical write still causing virtual sync, finite transition count, attention takeover, active relay restoration, automatic idle capacity, opt-out, reduced motion, sleep/wake, low power, device pinning, ENOSPC visibility, and no hardware path access.

- [ ] **Step 2: Capture RED**

Run:

```sh
PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_glance_policy.py tests/test_capacity_horizon.py tests/test_relay_motion.py tests/test_screen_bar_motion.py tests/test_sidepulse.py -q -k 'glance or capacity_horizon or relay or screen_bar or physical or virtual'
```

- [ ] **Step 3: Integrate one resolver before transport**

Resolve glance once per controller refresh. Pass its semantic program and elapsed phase to the physical writer and Screen Bar. Preserve per-device explicit Battery and Timer display choices; automatic capacity applies only to agent-status devices in quiet state.

- [ ] **Step 4: Add Settings controls**

Add an `Automatic glance` toggle and an idle-capacity option with clear semantic help. Reuse project spacing and switches. Do not add a mode matrix or separate per-provider animation settings.

- [ ] **Step 5: Run guarded source verification**

Run full Ruff, the Step 2 selection, attention/failure/completion neighbors, hardware guard, and diff check.

- [ ] **Step 6: Independently review Task 3**

Review semantic priority, infinite animation, full-line coverage, phase reset, dedupe, reduced motion, device-specific settings, failed writes, and privacy.

### Task 4: Candidate visual and physical acceptance

**Files:**
- Create: `.superpowers/sdd/2026-08-12-sidepulse-operator-experience/task-16-report.md`

- [ ] **Step 1: Build and verify a private candidate bundle**
- [ ] **Step 2: Record deterministic 120 fps Screen Bar fixtures for attention, active relay, completion, failure, capacity, and rest**
- [ ] **Step 3: Inspect frame pacing, full-line traversal, Alcove geometry, transition termination, and reduced motion**
- [ ] **Step 4: With explicit live-device authority, verify 2-LED and 8-LED physical semantics and failed-write reporting**
- [ ] **Step 5: Record source, package, candidate UI, installed app, and physical-device evidence separately**
