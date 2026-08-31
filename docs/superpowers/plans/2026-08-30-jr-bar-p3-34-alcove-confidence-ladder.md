# JR Bar P3.34 Alcove Confidence Ladder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` or `superpowers:executing-plans`
> to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Give Settings, Doctor, Screen Bar geometry, Screen Bar motion, and
accessibility one honest seven-state Alcove confidence contract.

**Architecture:** Keep capture outcomes factual and introduce one AppKit-free
projection in `alcove_observation.py`. Record geometry age with the existing
status snapshot, then make Settings, Doctor, and `VirtualStatusDevice` consume
the projection instead of deriving their own meanings. The view receives typed
silhouette and confidence values; only stale-to-fresh or recovering-to-fresh
Screen Bar geometry gets a bounded recovery settle.

**Tech Stack:** Python 3.12, PyObjC/AppKit, pytest, Ruff, native macOS
accessibility APIs.

**Spec:**
`docs/superpowers/specs/2026-08-30-jr-bar-p3-34-alcove-confidence-ladder-design.md`

## Global Constraints

- Preserve the dirty worktree and existing SidePulse compatibility identifiers.
- Do not add a production dependency.
- Do not commit, push, install, change permissions, publish, or deploy.
- Never prompt for Screen Recording outside the existing explicit Settings
  action.
- Confidence presentation must not alter physical LED output or semantic agent
  colors.
- Use the current 2-second observation freshness, 8-second geometry hold, and
  30-second status freshness limits.
- Reduce Motion replaces recovery animation with an immediate frame update.
- Use project-native controls, system typography, semantic colors, visible text,
  keyboard behavior, and accessibility metadata.
- Run focused tests within each task, `make fast` once the tranche stabilizes,
  and one full suite only at the completed priority boundary.

---

### Task 1: Pure seven-state projection

**Files:**

- Modify: `src/sidepulse/alcove_observation.py`
- Modify: `tests/test_alcove_observation.py`

**Interfaces:**

- Produces `AlcoveConfidenceState`, `AlcoveGeometryIntent`,
  `AlcoveMotionIntent`, `AlcoveConfidenceProjection`, and
  `project_alcove_confidence(...)`.
- Extends `AlcoveStatusSnapshot` and `note_alcove_status(...)` with validated
  last-good geometry evidence.
- Produces `AlcoveObservationReducer.last_good_age(now: float) -> float | None`.

- [x] **Step 1: Add failing state-table and boundary tests**

```python
@pytest.mark.parametrize(
    ("status", "age", "geometry_age", "expected"),
    [
        (AlcoveCaptureStatus.CAPTURED, 0.0, 0.0, AlcoveConfidenceState.FRESH),
        (AlcoveCaptureStatus.CAPTURED, 0.0, 2.01, AlcoveConfidenceState.STALE),
        (AlcoveCaptureStatus.IMAGE_UNUSABLE, 0.0, None, AlcoveConfidenceState.UNSUPPORTED),
        (AlcoveCaptureStatus.CAPTURE_FAILED, 0.0, None, AlcoveConfidenceState.RECOVERING),
    ],
)
def test_confidence_projection_resolves_raw_capture_facts(...):
    ...
```

Also cover following disabled, permission and window blockers outranking cached
success, a missing first result, exactly 2, 8, and 30 seconds, expired geometry,
non-finite clocks, exact visible copy, accessibility copy, geometry intent,
motion intent, and permission-action visibility.

- [x] **Step 2: Run the pure tests and record the expected red result**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_alcove_observation.py
```

Expected: new imports or assertions fail because the confidence contract does
not exist.

- [x] **Step 3: Implement the enums, dataclasses, validation, and resolver**

Use this public shape:

```python
def project_alcove_confidence(
    *,
    following: bool,
    snapshot: AlcoveStatusSnapshot | None,
    blocker: AlcoveCaptureStatus | None,
    now: float,
) -> AlcoveConfidenceProjection:
    ...
```

Compute effective geometry age as recorded geometry age plus elapsed snapshot
age. Never turn a negative, non-finite, or missing value into fresh evidence.
Keep state copy in one immutable table plus the two held-versus-fallback message
variants named in the spec.

- [x] **Step 4: Add reducer last-good age without changing hold behavior**

```python
def last_good_age(self, *, now: float) -> float | None:
    if self._last_good_at is None or not _finite(now):
        return None
    age = float(now) - self._last_good_at
    return age if age >= 0.0 else None
```

`current()` remains the authority that clears observations after eight seconds.

- [x] **Step 5: Run pure tests and Ruff**

```bash
.venv/bin/python -m pytest -q tests/test_alcove_observation.py
.venv/bin/ruff check src/sidepulse/alcove_observation.py tests/test_alcove_observation.py
```

Expected: pass.

---

### Task 2: Doctor consumes seven semantic states

**Files:**

- Modify: `src/sidepulse/doctor.py`
- Modify: `tests/test_alcove_honesty.py`

**Interfaces:**

- Consumes `project_alcove_confidence(...)` and
  `AlcoveConfidenceProjection.state`.
- Produces Doctor document version 4 and fixed codes `stale`, `recovering`, and
  `unsupported`.

- [x] **Step 1: Write failing manifest and seven-code tests**

```python
@pytest.mark.parametrize(
    ("state", "code"),
    [
        (AlcoveConfidenceState.FRESH, DiagnosticCode.HEALTHY),
        (AlcoveConfidenceState.STALE, DiagnosticCode.STALE),
        (AlcoveConfidenceState.PERMISSION_DENIED, DiagnosticCode.NOT_PERMITTED),
        (AlcoveConfidenceState.DISCONNECTED, DiagnosticCode.NOT_RUNNING),
        (AlcoveConfidenceState.UNSUPPORTED, DiagnosticCode.UNSUPPORTED),
        (AlcoveConfidenceState.NOT_FOLLOWING, DiagnosticCode.NOT_CONFIGURED),
        (AlcoveConfidenceState.RECOVERING, DiagnosticCode.RECOVERING),
    ],
)
def test_doctor_maps_every_confidence_state(...):
    ...
```

Assert `DOCTOR_VERSION == 4`, the Alcove field manifest permits all seven codes,
and an expired captured snapshot emits stale rather than unavailable or healthy.

- [x] **Step 2: Run the Doctor slice and record red**

```bash
.venv/bin/python -m pytest -q tests/test_alcove_honesty.py -k "doctor or diagnostic"
```

Expected: missing codes, old document version, or old raw-status mapping fails.

- [x] **Step 3: Replace the raw mapping with the semantic mapping**

`_alcove_follow_state_probe()` must gather the promptless blocker, call the pure
projection, and map its state exactly once. Keep the diagnostic payload
content-free and count healthy only for `FRESH`.

- [x] **Step 4: Run Doctor tests and Ruff**

```bash
.venv/bin/python -m pytest -q tests/test_alcove_honesty.py -k "doctor or diagnostic"
.venv/bin/ruff check src/sidepulse/doctor.py tests/test_alcove_honesty.py
```

Expected: pass.

---

### Task 3: Settings text and action projection

**Files:**

- Modify: `src/sidepulse/settings_window.py`
- Modify: `tests/test_alcove_honesty.py`
- Modify: `tests/test_settings_accessibility.py`

**Interfaces:**

- Consumes `project_alcove_confidence(...)`.
- Produces `alcove_follow_projection(target) -> AlcoveConfidenceProjection`.
- Preserves `alcove_follow_state`, `alcove_follow_status_text`, and
  `alcove_follow_needs_permission` as compatibility adapters over the projection.

- [x] **Step 1: Add failing Settings tests for all seven states**

For each state, assert exact visible text, stable state prefix, no color-only
meaning, and permission-button visibility only for permission denied. Add a
stale snapshot case and a successful-permission-reset case that reads recovering
instead of the ambiguous `No measurement yet.`

- [x] **Step 2: Run the Settings slice and record red**

```bash
.venv/bin/python -m pytest -q \
  tests/test_alcove_honesty.py \
  tests/test_settings_accessibility.py \
  -k "alcove"
```

Expected: old raw status copy or `No measurement yet.` assertions fail.

- [x] **Step 3: Implement one Settings adapter over the pure projection**

```python
def alcove_follow_projection(target) -> AlcoveConfidenceProjection:
    following = bool(target.settings.screen_bar_follow_alcove)
    blocker = alcove_follow_blocker(following=following)
    return project_alcove_confidence(
        following=following,
        snapshot=latest_alcove_status(),
        blocker=blocker,
        now=time.monotonic(),
    )
```

Do not probe AppKit or permission state more than once per refresh call. The
permission button reads `projection.needs_permission_action`.

- [x] **Step 4: Verify accessibility metadata on the native row**

The status text must expose the state label and explanation to VoiceOver. The
button keeps its existing explicit label and help and stays hidden for the
other six states.

- [x] **Step 5: Run the Settings tests and Ruff**

```bash
.venv/bin/python -m pytest -q \
  tests/test_alcove_honesty.py \
  tests/test_settings_accessibility.py \
  -k "alcove"
.venv/bin/ruff check src/sidepulse/settings_window.py tests/test_alcove_honesty.py tests/test_settings_accessibility.py
```

Expected: pass.

---

### Task 4: Typed silhouette and Screen Bar confidence handoff

**Files:**

- Modify: `src/sidepulse/alcove_observation.py`
- Modify: `src/sidepulse/virtual_device.py`
- Modify: `tests/test_alcove_observation.py`
- Modify: `tests/test_screen_bar_motion.py`
- Modify: `tests/test_alcove_honesty.py`

**Interfaces:**

- Produces `AlcoveSilhouette` with immutable, validated geometry.
- `VirtualLedView.setAlcoveSilhouette_` accepts `AlcoveSilhouette | None`.
- `VirtualLedView.setAlcoveConfidence_` accepts
  `AlcoveConfidenceProjection` and change-gates accessibility updates.

- [x] **Step 1: Add failing validation and view-boundary tests**

Reject non-finite center, width, height, mutable or malformed contours, and raw
tuples. Accept an exact valid silhouette and prove repeated identical values do
not dirty the view twice.

- [x] **Step 2: Run the view-boundary tests and record red**

```bash
.venv/bin/python -m pytest -q \
  tests/test_alcove_observation.py \
  tests/test_screen_bar_motion.py \
  -k "alcove or silhouette"
```

- [x] **Step 3: Implement the typed silhouette and confidence setters**

Keep `VirtualLedView` storage plain and immutable. Confidence updates set:

```python
view.setAccessibilityLabel_("JR Bar Screen Bar, Alcove following")
view.setAccessibilityValue_(projection.accessibility_value)
view.setAccessibilityHelp_(projection.accessibility_help)
```

Guard missing accessibility selectors so headless doubles remain supported.

- [x] **Step 4: Project confidence in `reposition()`**

Pass the reducer's last-good age into `note_alcove_status`, call the pure
projection once per reposition, obey its geometry intent, and send the same
projection to the view. Unsupported geometry must clear the silhouette and use
Screen Bar geometry immediately.

- [x] **Step 5: Run the focused Screen Bar tests and Ruff**

```bash
.venv/bin/python -m pytest -q \
  tests/test_alcove_observation.py \
  tests/test_alcove_honesty.py \
  tests/test_screen_bar_motion.py
.venv/bin/ruff check \
  src/sidepulse/alcove_observation.py \
  src/sidepulse/virtual_device.py \
  tests/test_alcove_observation.py \
  tests/test_alcove_honesty.py \
  tests/test_screen_bar_motion.py
```

Expected: pass.

---

### Task 5: Recovery settle and Reduce Motion substitution

**Files:**

- Modify: `src/sidepulse/virtual_device.py`
- Modify: `tests/test_screen_bar_motion.py`

**Interfaces:**

- Consumes prior and current `AlcoveConfidenceState` plus
  `AccessibilityDisplayPreferences.reduce_motion`.
- Produces one bounded 180-millisecond on-screen frame settle only for stale or
  recovering to fresh transitions.

- [x] **Step 1: Verify the AppKit animation API**

Use Context7 or official Apple documentation to verify the current
`NSAnimationContext` and window animator calls already used by the project. Do
not add a dependency or guess a selector.

- [x] **Step 2: Add failing transition tests**

Cover:

- recovering to fresh starts exactly one 0.18-second ease-out settle;
- stale to fresh starts exactly one settle;
- fresh to fresh does not animate;
- every blocker transition is immediate;
- Reduce Motion makes recovery immediate;
- animation setup failure still applies the requested frame once.

- [x] **Step 3: Run transition tests and record red**

```bash
.venv/bin/python -m pytest -q tests/test_screen_bar_motion.py -k "alcove and (recover or motion)"
```

- [x] **Step 4: Implement the smallest verified AppKit adapter**

Keep the transition decision pure and place AppKit calls in one adapter near
the existing announcer animation boundary. Store only the last confidence
state and never start a timer, sampler, or physical-device command.

- [x] **Step 5: Run motion, lifecycle, and accessibility tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_screen_bar_motion.py \
  tests/test_alcove_honesty.py \
  tests/test_settings_accessibility.py \
  tests/test_status_bar_lifecycle_contract.py
```

Expected: pass.

---

### Task 6: Native rendering, gates, review, and receipts

**Files:**

- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/FEATURE-MATRIX.md`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`
- Modify: this plan

**Interfaces:**

- Produces source-render evidence for all seven states in Aqua and Dark Aqua.
- Produces final source verification and review receipts without claiming
  installed-app or permission proof.

- [x] **Step 1: Load Impeccable's craft floor immediately before UI edits**

Apply the native operational-mode rules: incumbent visual identity, system
controls, 4/8-point rhythm, explicit state text, visible focus, no color-only
meaning, and bounded visual-QA passes.

- [x] **Step 2: Render all seven states in an isolated AppKit process**

Build the actual Screen Bar Settings pane, inject each projection, render in
`NSAppearanceNameAqua` and `NSAppearanceNameDarkAqua`, and capture native bitmap
evidence. Inspect for clipping, contrast, state-label visibility, permission
button state, and stable layout. Render recovering once with Reduce Motion off
and once on to inspect the transition substitution.

- [x] **Step 3: Run the Impeccable detector once over changed UI targets**

```bash
node /Users/jonathanreed/.agents/skills/impeccable/scripts/detect.mjs --json \
  src/sidepulse/settings_window.py src/sidepulse/virtual_device.py
```

Fix only findings relevant to the changed surface.

- [x] **Step 4: Run focused and canonical gates**

```bash
.venv/bin/python -m pytest -q \
  tests/test_alcove_observation.py \
  tests/test_alcove_honesty.py \
  tests/test_screen_bar_motion.py \
  tests/test_settings_accessibility.py \
  tests/test_status_bar_lifecycle_contract.py
.venv/bin/ruff check \
  src/sidepulse/alcove_observation.py \
  src/sidepulse/doctor.py \
  src/sidepulse/settings_window.py \
  src/sidepulse/virtual_device.py
git diff --check
make fast
```

- [x] **Step 5: Obtain findings-first independent review**

Review state precedence, age arithmetic, hold expiry, no-prompt privacy,
diagnostic compatibility, typed view validation, transition idempotence, Reduce
Motion, accessibility, and test isolation. Fix validated findings and rerun the
affected focused gates.

- [x] **Step 6: Run one stable-fingerprint complete suite**

Record the sorted `src/` and `tests/` file count and SHA-256 fingerprint, run:

```bash
.venv/bin/python -m pytest -q -W error::pytest.PytestUnhandledThreadExceptionWarning
```

Recompute the fingerprint and require it to match.

- [x] **Step 7: Record receipts and leave external gates explicit**

Mark source rows complete only after the stable run and no-findings rereview.
Record installed-app, real permission, live Alcove, signing, notarization, and
release checks as external or blocked rather than inferred.
