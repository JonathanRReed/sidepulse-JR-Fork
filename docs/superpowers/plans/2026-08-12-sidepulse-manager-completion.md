# SidePulse Manager Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SidePulse a trustworthy all-agent and hardware manager whose lifecycle truth, finite signals, menu, Screen Bar, physical devices, usage, notifications, performance, local data, and packaged runtime agree.

**Architecture:** Normalize provider records into durable lifecycle facts, project those facts once into user-actionable attention and visible rows, and layer edge-triggered finite signals over that projection. Keep refresh, delivery, rendering cadence, retention, and packaging decisions in small pure policies with explicit tests, while existing AppKit and hardware adapters become consumers rather than semantic authorities.

**Tech Stack:** Python 3.10+, dataclasses, PyObjC/AppKit/CoreGraphics, SidePulse LED DSL/WASM, unittest/pytest, Ruff, macOS launchd and code-signing tools.

## Global Constraints

- A non-actionable failure plays exactly two repetitions and never becomes persistent Ask.
- Persistent attention is reserved for a live actionable approval or input request.
- Subagent attention persists only when `subagent_asks_alert` is enabled.
- All surfaces consume the same semantic projection; renderers do not reclassify raw provider statuses.
- Warm or restored state never replays a finite signal.
- A finite signal restores the latest lifecycle display without waiting for another provider event.
- Preserve all completions in a poll and keep visual, notification, and webhook delivery settings independent.
- Use the project design system and current signal palette before adding new styling.
- Do not add a production dependency without user approval.
- Do not commit, push, deploy, publish, or open a pull request.
- Preserve unrelated user state and create recoverable backups before replacing an installed artifact.
- Use TDD for every behavior change: write the test, observe the expected failure, implement minimally, and observe the pass.
- Run Ruff, the full test suite, and actual rendered/hardware verification before terminal completion.

---

### Task 1: Authoritative attention projection

**Files:**
- Create: `src/sidepulse/attention.py`
- Modify: `src/sidepulse/models.py`
- Modify: `src/sidepulse/collector.py`
- Test: `tests/test_attention.py`
- Test: `tests/test_sidepulse.py`

**Interfaces:**
- Consumes: `AgentStatus`, `AgentMode`, monitor snapshot fields, and `AgentMonitorSettings.subagent_asks_alert`.
- Produces: `SignalKind`, `LifecycleMode`, `ProjectedAgentRow`, `AttentionProjection`, `stable_event_key(status)`, and `project_attention(snapshot, settings, consumed_event_keys=()) -> AttentionProjection`.

- [ ] **Step 1: Write failing projection-table tests**

```python
def test_terminal_tool_failure_is_visible_but_not_actionable():
    snapshot = snapshot_with(status(event_name="PostToolUseFailure", mode=AgentMode.BLOCKED_ERROR))
    projection = project_attention(snapshot, AgentMonitorSettings())
    assert projection.actionable_attention == ()
    assert projection.transient_signals[0].kind is SignalKind.FAILURE
    assert projection.transient_signals[0].repetitions == 2

def test_main_permission_request_is_persistent_attention():
    snapshot = snapshot_with(status(event_name="PermissionRequest", mode=AgentMode.WAITING_FOR_INPUT))
    projection = project_attention(snapshot, AgentMonitorSettings())
    assert len(projection.actionable_attention) == 1

def test_subagent_attention_obeys_one_setting_everywhere():
    snapshot = snapshot_with(subagent_status(event_name="PermissionRequest"))
    assert project_attention(snapshot, AgentMonitorSettings()).actionable_attention == ()
    enabled = replace(AgentMonitorSettings(), subagent_asks_alert=True)
    assert len(project_attention(snapshot, enabled).actionable_attention) == 1
```

- [ ] **Step 2: Run the tests and observe the expected import or assertion failures**

Run: `PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_attention.py -q`

Expected: failure because `sidepulse.attention` and the projection types do not exist.

- [ ] **Step 3: Implement the pure projection and explicit actionability**

```python
class SignalKind(str, Enum):
    FAILURE = "failure"

@dataclass(frozen=True)
class TransientSignal:
    event_key: str
    kind: SignalKind
    repetitions: int
    source_agent_id: str | None

def actionable_request(status: AgentStatus, settings: AgentMonitorSettings) -> bool:
    if status.is_subagent and not settings.subagent_asks_alert:
        return False
    return status.mode == AgentMode.WAITING_FOR_INPUT and status.event_name in {
        "PermissionRequest",
        "Notification",
    }
```

Implement `project_attention` so failed rows remain visible, only proven live requests populate attention, and each new failure creates `TransientSignal(event_key=stable_event_key(status), kind=SignalKind.FAILURE, repetitions=2, source_agent_id=status.agent_id)`.

- [ ] **Step 4: Run projection tests and existing collector/model tests**

Run: `PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_attention.py tests/test_sidepulse.py -q -k 'AgentMonitor or ask_statuses or display_aggregate or blocked or permission'`

Expected: all selected tests pass with old tests updated only where their semantics were incorrect.

- [ ] **Step 5: Record the red-green commands and diff summary in the task report without committing**

The report must include the failing command and reason, the passing command and count, changed files, and any semantics still implemented outside the projection.

### Task 2: Two-repetition signal coordinator and cross-surface routing

**Files:**
- Create: `src/sidepulse/signal_coordinator.py`
- Modify: `src/sidepulse/status_bar.py`
- Modify: `src/sidepulse/colors.py`
- Modify: `src/sidepulse/led_status.py`
- Modify: `src/sidepulse/virtual_device.py`
- Test: `tests/test_signal_coordinator.py`
- Test: `tests/test_sidepulse.py`

**Interfaces:**
- Consumes: `AttentionProjection` and `TransientSignal` from Task 1.
- Produces: `FiniteSignalCoordinator.observe(projection, now)`, `FiniteSignalCoordinator.active(now)`, `FiniteSignalCoordinator.next_deadline`, and renderer inputs containing projected lifecycle plus an optional active signal.

- [ ] **Step 1: Write failing coordinator and cross-surface contract tests**

```python
def test_failure_plays_two_repetitions_then_restores_without_new_snapshot():
    coordinator = FiniteSignalCoordinator(failure_cycle_seconds=1.0)
    coordinator.observe(projection_with_failure("event-1"), now=10.0)
    assert coordinator.active(10.0) is not None
    assert coordinator.active(11.99) is not None
    assert coordinator.active(12.0) is None

def test_warm_state_does_not_replay_failure():
    coordinator = FiniteSignalCoordinator(failure_cycle_seconds=1.0)
    coordinator.establish_watermark(projection_with_failure("old-event"))
    coordinator.observe(projection_with_failure("old-event"), now=20.0)
    assert coordinator.active(20.0) is None
```

Add controller tests proving the menu icon, ask badge, physical LED display kind, virtual display state, pinned display input, click target, and escalation all read the same projection for a stopped subagent failure and a live main permission request.

- [ ] **Step 2: Run the new tests and observe failure because finite coordination and projected routing are absent**

Run: `PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_signal_coordinator.py tests/test_sidepulse.py -q -k 'failure_signal or projection_contract'`

Expected: failure on missing coordinator or raw-status renderer disagreement.

- [ ] **Step 3: Implement bounded edge playback and timer invalidation**

```python
class FiniteSignalCoordinator:
    def observe(self, projection: AttentionProjection, now: float) -> bool:
        candidate = next(
            (signal for signal in projection.transient_signals if signal.event_key not in self.consumed_keys),
            None,
        )
        if candidate is None:
            return False
        self.consumed_keys.add(candidate.event_key)
        self._active = ActiveSignal(
            signal=candidate,
            started_at=now,
            ends_at=now + self.failure_cycle_seconds * candidate.repetitions,
        )
        return True

    def active(self, now: float) -> ActiveSignal | None:
        if self._active is not None and now >= self._active.ends_at:
            self._active = None
        return self._active
```

Use an AppKit timer scheduled for `next_deadline` to invoke a render refresh when the second repetition ends. Bound consumed keys and queued distinct failures by count and age. Do not extend playback for duplicate records.

- [ ] **Step 4: Route every surface through `AttentionProjection`**

Replace raw semantic choices in `display_aggregate_mode`, `ask_statuses`, LED synchronization, virtual-device synchronization, pinned devices, click target, and escalation tracking with projection fields. Renderer helpers may still consume projected rows for per-agent color blending, but cannot infer Ask from `BLOCKED_ERROR`.

- [ ] **Step 5: Run targeted tests and the complete suite**

Run: `ruff check src tests`

Run: `PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_attention.py tests/test_signal_coordinator.py tests/test_sidepulse.py -q`

Expected: all checks pass and the original stopped-worker reproduction is encoded as a regression test.

- [ ] **Step 6: Record the tranche receipt without committing**

Include timer behavior, consumed-key bounds, cross-surface consumers, red-green output, full selected-suite output, and remaining live acceptance steps.

### Task 3: Completion detection and independent batch delivery

**Files:**
- Create: `src/sidepulse/completions.py`
- Modify: `src/sidepulse/status_bar.py`
- Test: `tests/test_completions.py`
- Test: `tests/test_sidepulse.py`

**Interfaces:**
- Consumes: current statuses, prior mode map, freshness policy, delivery settings, and existing visual, notification, and webhook adapters.
- Produces: `CompletionBatch`, `detect_completion_batch(previous_modes, statuses, now)`, and independent visual, notification, and webhook dispatch loops.

- [ ] **Step 1: Write failing setting-matrix and same-poll batch tests**

```python
def test_notifications_still_deliver_when_visual_sweep_is_disabled():
    controller.settings = replace(controller.settings, completion_sweep_enabled=False, completion_notification_enabled=True)
    controller.track_completions((working_status("a"),))
    controller.track_completions((completed_status("a"),))
    assert posted_ids == ["a"]
    assert controller.completion_sweep_until == 0.0

def test_two_same_poll_completions_are_both_delivered():
    controller.track_completions((working_status("a"), working_status("b")))
    controller.track_completions((completed_status("a"), completed_status("b")))
    assert posted_ids == ["a", "b"]
    assert webhook_ids == ["a", "b"]
```

- [ ] **Step 2: Run and observe failures from the shared early return and `finished[-1]` behavior**

Run: `PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_completions.py tests/test_sidepulse.py -q -k completion`

- [ ] **Step 3: Implement complete deterministic batches and channel isolation**

Detect once, sort by stable agent identity, and send the full batch to each independently enabled channel. Keep visual sweep color selection deterministic without discarding delivery items. Keep retry or failure state local to each channel.

- [ ] **Step 4: Run completion tests and record the receipt**

Run: `ruff check src tests && PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_completions.py tests/test_sidepulse.py -q -k completion`

Expected: all completion tests pass, including disabled-sweep notifications and two-completion batches.

### Task 4: Freshness, pruning, and bounded caches

**Files:**
- Create: `src/sidepulse/freshness.py`
- Modify: `src/sidepulse/collector.py`
- Modify: `src/sidepulse/status_bar.py`
- Modify: `src/sidepulse/usage_stats.py`
- Test: `tests/test_freshness.py`
- Test: `tests/test_sidepulse.py`

**Interfaces:**
- Produces: `bounded_age_seconds(now, timestamp)`, `is_recent(now, timestamp, window_seconds)`, snapshot-time pruning, and bounded transcript, event-key, and usage cache utilities.

- [x] **Step 1: Write failing future-time, no-new-ingest, and cache-rotation tests**

```python
def test_future_completion_is_not_recent():
    assert not is_recent(now, now + timedelta(minutes=10), 120.0)

def test_snapshot_prunes_expired_loaded_state_without_new_ingest():
    monitor = monitor_loaded_with(old_status)
    assert monitor.snapshot().statuses == ()
    assert old_status.agent_id not in monitor.statuses_by_key

def test_transcript_cache_evicts_files_outside_recent_scan_set():
    cache = cache_after_rotating_files(limit=24, rotations=200)
    assert len(cache) <= 24
```

- [x] **Step 2: Run and observe the current signed-delta and ingest-only pruning failures**

- [x] **Step 3: Implement one freshness policy and bounded cache retention**

Use the same helper for completion visibility, recent rows, collector staleness, and warm-state signal eligibility. Reject implausibly future-dated events from recent or actionable projections. Prune loaded monitor state inside the snapshot lock, and delete cache entries no longer eligible by count and age.

- [x] **Step 4: Run freshness, collector, usage, and full tests**

Run: `ruff check src tests`

Run: `PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_freshness.py tests/test_sidepulse.py -q`

Expected: all tests pass with bounded in-memory and persisted caches.

### Task 5: Provider-aware usage refresh and live menu card

**Files:**
- Create: `src/sidepulse/refresh_policy.py`
- Create: `src/sidepulse/usage_view.py`
- Modify: `src/sidepulse/status_bar.py`
- Modify: `src/sidepulse/usage_stats.py`
- Modify: `src/sidepulse/claude_quota.py`
- Test: `tests/test_refresh_policy.py`
- Test: `tests/test_usage_view.py`
- Test: `tests/test_sidepulse.py`

**Interfaces:**
- Produces: `plan_menu_open_refresh(provider_states, now, low_power) -> RefreshPlan`, `UsageWindowViewModel`, and a stable menu-card host updated in place.

- [x] **Step 1: Write failing pure policy tests**

```python
def test_menu_open_refreshes_only_stale_or_missing_providers():
    plan = plan_menu_open_refresh(states, now=1000.0, low_power=False)
    assert plan.provider_ids == ("codex", "claude")

def test_in_flight_and_backoff_providers_are_not_duplicated():
    assert plan_menu_open_refresh(in_flight_and_failed_states, now=1000.0, low_power=False).provider_ids == ()
```

Add view-model tests for actual `window_minutes`, reset time, secondary limits, stale state, missing data, and provider error text. No test may expect the hard-coded word “weekly” unless the duration is seven days.

- [x] **Step 2: Observe current failures from fixed polling and missing menu usage**

- [x] **Step 3: Implement per-provider policy and stable live menu content**

On `menuWillOpen_`, request only stale or missing providers. Deduplicate in-flight work, apply bounded exponential backoff, and update the existing hosted view model without rebuilding the menu. Keep Settings usage charts and the dropdown card fed by the same payload.

- [x] **Step 4: Run policy, view-model, menu, and usage tests**

Run: `ruff check src tests && PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_refresh_policy.py tests/test_usage_view.py tests/test_sidepulse.py -q -k 'usage or quota or menu'`

Expected: all selected tests pass and menu-open behavior is deterministic.

### Task 6: Modern notification authorization and delivery

**Files:**
- Create: `src/sidepulse/macos_notifications.py`
- Modify: `src/sidepulse/status_bar.py`
- Modify: `src/sidepulse/settings_window.py`
- Modify: `pyproject.toml` only if the approved implementation cannot bridge UserNotifications with installed dependencies
- Test: `tests/test_macos_notifications.py`
- Test: `tests/test_sidepulse.py`

**Interfaces:**
- Produces: `NotificationAuthorizationState`, `MacOSNotificationClient.authorization_state()`, `request_authorization()`, and `deliver(identifier, title, body, user_info)`.

- [ ] **Step 1: Verify the UserNotifications API against current primary documentation and record whether dynamic PyObjC bridging works without a dependency**

Do not guess selectors or authorization values. If `pyobjc-framework-UserNotifications` is required, stop this task and request approval before changing `pyproject.toml`.

- [ ] **Step 2: Write failing adapter and settings-state tests using an injected center**

```python
def test_delivery_requires_authorized_state():
    client = MacOSNotificationClient(center=denied_center)
    assert client.deliver("completion:a", "Finished", "Agent a finished", {}) is False

def test_request_is_only_started_by_explicit_user_action():
    controller.applicationDidFinishLaunching_(None)
    assert center.request_count == 0
    controller.requestNotificationPermission_(None)
    assert center.request_count == 1
```

- [ ] **Step 3: Implement stable identifiers, explicit authorization, diagnostics, and click routing**

Remove `NSUserNotification` delivery and delegate setup. Preserve completion click-to-session behavior through notification response user info. Settings must show authorized, denied, not determined, or unavailable without prompting at launch.

- [ ] **Step 4: Run notification and controller tests**

Run: `ruff check src tests && PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_macos_notifications.py tests/test_sidepulse.py -q -k notification`

Expected: modern adapter tests pass with no launch-time prompt.

### Task 7: Adaptive Screen Bar rendering and measured draw reduction

**Files:**
- Create: `src/sidepulse/render_policy.py`
- Modify: `src/sidepulse/virtual_device.py`
- Modify: `src/sidepulse/status_bar.py`
- Test: `tests/test_render_policy.py`
- Test: `tests/test_sidepulse.py`

**Interfaces:**
- Produces: `RenderEnvironment`, `RenderCadence`, `choose_render_cadence(environment, animation_active)`, and cached glow or riser geometry keyed by dimensions, brightness, and quantized colors.

- [ ] **Step 1: Write failing adaptive-policy and cache-reuse tests**

```python
def test_hidden_or_sleeping_surface_pauses():
    assert choose_render_cadence(RenderEnvironment(visible=False), False).fps == 0.0
    assert choose_render_cadence(RenderEnvironment(display_asleep=True), True).fps == 0.0

def test_low_power_and_thermal_pressure_reduce_cadence():
    cadence = choose_render_cadence(RenderEnvironment(visible=True, low_power=True, thermal="serious"), True)
    assert cadence.fps <= 10.0

def test_static_glow_geometry_is_reused():
    first = build_glow_geometry(cache, key)
    second = build_glow_geometry(cache, key)
    assert first is second
```

- [ ] **Step 2: Observe failures because cadence is constant and geometry is rebuilt**

- [ ] **Step 3: Implement the pure cadence table, pause rules, and bounded geometry cache**

Use public macOS state when available and injectable fallbacks otherwise. Do not lower visible active quality blindly. Invalidate cached geometry only when dimensions, brightness, or quantized colors change. Sample provider or WASM data no faster than the chosen visible cadence can consume.

- [ ] **Step 4: Run renderer tests, then record fixed baseline performance scenarios**

Run: `ruff check src tests && PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_render_policy.py tests/test_sidepulse.py -q -k 'virtual or render or screen_bar'`

After install, measure hidden/menu-closed, visible-static, and active-animation CPU for 60 seconds each. Record median and peak RSS using the same commands and display configuration.

### Task 8: Owner-only state, redaction, and bounded retention

**Files:**
- Create: `src/sidepulse/private_io.py`
- Modify: `src/sidepulse/settings.py`
- Modify: `src/sidepulse/hook.py`
- Modify: `src/sidepulse/collector.py`
- Modify: `src/sidepulse/audit.py`
- Modify: `src/sidepulse/usage_stats.py`
- Modify: `src/sidepulse/ipc.py`
- Modify: `src/sidepulse/install.py`
- Test: `tests/test_private_io.py`
- Test: `tests/test_sidepulse.py`

**Interfaces:**
- Produces: `ensure_private_directory(path)`, `atomic_private_write(path, data)`, `redact_event_payload(payload)`, and `enforce_retention(root, policy)`.

- [ ] **Step 1: Write failing mode, migration, redaction, and retention tests**

```python
def test_private_write_ignores_ambient_umask():
    atomic_private_write(path, "secret")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

def test_existing_broad_modes_are_migrated_without_data_loss():
    path.write_text("kept")
    path.chmod(0o644)
    ensure_private_file(path)
    assert path.read_text() == "kept"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
```

Add redaction cases for authorization headers, tokens, cookies, webhook URLs, raw prompt bodies, and duplicated `raw_preview`. Add deterministic size and age retention cases.

- [ ] **Step 2: Observe current permission and raw-field failures**

- [ ] **Step 3: Route sensitive state through private I/O and minimize payloads**

Use explicit file descriptors or post-create chmod safely, preserve atomic replacement, set socket mode before accepting clients, migrate existing state on launch, and rotate before unbounded growth. Keep only fields required for provider state and bounded diagnostics.

- [ ] **Step 4: Run privacy, settings, hook, collector, usage, IPC, and audit tests**

Run: `ruff check src tests && PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_private_io.py tests/test_sidepulse.py -q -k 'settings or hook or state or audit or ipc or usage'`

Expected: selected tests pass and no test artifact writes to live user state or mounted hardware.

### Task 9: Self-contained signed runtime and trusted command paths

**Files:**
- Modify: `src/sidepulse/app_bundle.py`
- Modify: `src/sidepulse/status_bar_launch.py`
- Create: `src/sidepulse/trusted_tools.py`
- Modify: security-sensitive call sites using bare macOS system commands under `src/sidepulse/`
- Modify: `packaging/build_macos_pkg.sh`
- Create: `packaging/verify_macos_app.py`
- Test: `tests/test_app_bundle_security.py`
- Test: `tests/test_sidepulse.py`

**Interfaces:**
- Produces: one explicit production-bundle contract around the repository's existing PyInstaller/pkg builder; `trusted_system_tool(name) -> Path`; `verify_packaged_app(bundle) -> BundleVerification`; and a launch-agent contract that rejects mutable development wrappers as installed production runtimes.
- Preserves: source-tree and explicit-interpreter development runs, but never silently promotes them to the installed production LaunchAgent path.

- [ ] **Step 1: Write failing production-boundary, bundle-verification, environment, and command-path tests**

```python
def test_packaged_bundle_rejects_external_python_environment():
    result = verify_packaged_app(bundle_with_external_pythonpath)
    assert not result.accepted

def test_launch_path_excludes_user_writable_bin_directories():
    assert launch_agent_path_env(executable) == "/usr/bin:/bin:/usr/sbin:/sbin"
```

Add tests that:

- a non-frozen source install without an explicitly supplied development interpreter cannot synthesize the production LaunchAgent from the mutable Homebrew/venv wrapper;
- a frozen PyInstaller executable generates the production LaunchAgent with no `PYTHONHOME` or `PYTHONPATH`;
- the verifier rejects `LSEnvironment` Python overrides, absolute Homebrew/user/workspace load commands, missing internal Python/runtime payloads, mismatched identity, invalid signatures, and unexpected external import roots;
- the verifier accepts only Apple system libraries plus bundle-relative load commands;
- `security`, `ioreg`, `system_profiler`, `shortcuts`, `launchctl`, `codesign`, `clang`, `tail`, `open`, and `osascript` resolve to explicit trusted absolute paths or fail closed;
- a substituted executable, user-writable ancestor, symlink, non-regular file, non-root owner, or missing executable is rejected;
- user-selected applications and the separately discovered Codex CLI remain explicit user-tool paths and never enter the trusted-system-tool allowlist.

- [ ] **Step 2: Observe failures from the external boot shim, venv site-packages, and user-first `PATH`**

- [ ] **Step 3: Make the PyInstaller/pkg flow the only production bundle contract**

Do not attempt to turn the local TCC wrapper into a second package builder. The current wrapper intentionally imports from Homebrew, an external state-directory boot shim, and venv `site-packages`; preserve it only as an explicit development helper or retire it from production launch installation. Production installation must consume the existing PyInstaller app produced by `packaging/build_macos_pkg.sh`, using the same `io.sidepulse.app` identity as the application and LaunchAgent contract. A non-frozen source command must fail closed with build/install guidance unless the caller explicitly supplies a development interpreter for a development-only LaunchAgent.

Add `verify_packaged_app()` and make the packaging flow call it before `pkgbuild`. Verify the executable, internal Python/runtime payload, Info.plist identity and environment, nested Mach-O load commands, signature, and absence of mutable external Python/import roots. Build tools may remain build-time dependencies in the isolated build directory; none may become a runtime dependency or load path. Preserve the current recoverable install flow and defer replacement of the installed app to Task 10.

- [ ] **Step 4: Replace bare security-sensitive tool lookups**

Centralize a fixed macOS allowlist in `trusted_tools.py`, validate the resolved object and its ancestors, and pass absolute paths to subprocess calls. Keep user-configured application openers, the Codex CLI, and other account-owned executables separate from privileged or security-sensitive system tools. LaunchAgents receive only the system path; individual user tools must already be resolved to an explicit path before execution.

- [ ] **Step 5: Run bundle, launch, subprocess, and complete tests**

Run: `ruff check src tests`

Run: `PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_app_bundle_security.py tests/test_sidepulse.py -q`

Build the unsigned local-verification app into a temporary destination with `ALLOW_UNSIGNED=1`, run the repository verifier plus `codesign --verify --deep --strict`, inspect every nested Mach-O load command and runtime import root, and launch the temporary candidate without replacing the installed app. Record unavailable Developer ID or notarization credentials as a package-layer external gate, never as a source failure or inferred pass.

### Task 10: Installed runtime, rendered UI, hardware, performance, and security acceptance

**Files:**
- Create: `docs/verification/2026-08-12-sidepulse-manager-acceptance.md`
- Modify: source or tests only when an acceptance failure is first reproduced by a failing test.

**Interfaces:**
- Consumes: all prior tasks and the repository's existing installer, status logs, Screen Bar, and mounted SidePulse devices.
- Produces: a source, package, installed-runtime, rendered-interface, hardware, performance, and security receipt matrix.

- [ ] **Step 1: Run the complete static and automated suite**

Run: `ruff check src tests`

Run: `PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/ -q`

Expected: zero lint findings and zero test failures.

- [ ] **Step 2: Build and verify the candidate package before installation**

Record source revision or diff hash, candidate bundle hash, signature verification, internal import roots, trusted command paths, and private local modes. Stop installation if any package gate fails.

- [ ] **Step 3: Back up and replace the installed test application through the repository's supported flow**

Record the recoverable backup path, stop only the SidePulse process, install the verified candidate, relaunch it, and confirm the running executable and source identity belong to the candidate.

- [ ] **Step 4: Execute the lifecycle and signal scenario matrix**

Observe menu, icon, Screen Bar, and every mounted physical device for idle, working, non-actionable failure, main approval, main input, subagent approval with alerts off and on, completion, simultaneous completion, stale worker, provider error, and recovery. Time the failure signal to exactly two repetitions and confirm restoration without another event.

- [ ] **Step 5: Execute usage, notification, and error-state scenarios**

Open the menu with fresh, stale, missing, in-flight, and failed provider data; confirm stable card updates and correct windows. Verify notification states without an implicit prompt, then exercise authorized delivery and click routing if the OS is already authorized or after the user handles the explicit permission action.

- [ ] **Step 6: Execute fixed performance and soak scenarios**

Measure 60-second hidden, static-visible, active, Ask, failure, and completion scenarios. Run a 30-minute provider-file rotation and verify cache bounds, no monotonic memory growth, no retained finite signal, and no repeated hardware writes after a signal ends.

- [ ] **Step 7: Rescan security and inspect retained data**

Repeat the package/runtime security scan, inspect file and socket modes, verify redaction and retention, and confirm no external mutable import path or user-writable system-tool resolution remains.

- [ ] **Step 8: Reconcile every acceptance gate and record remaining external blockers**

The work is complete only when functionality, rendered UX, runtime performance, data correctness, security/privacy, packaging/trust, and connected hardware gates have direct receipts. An unavailable device or user-controlled permission remains an explicit external blocker, never an inferred pass.
