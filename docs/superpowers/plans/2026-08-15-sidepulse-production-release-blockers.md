# SidePulse Production Release Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the seven confirmed release blockers from the 2026-08-15 deep production audit without widening product scope or changing the application identity.

**Architecture:** Preserve the current canonical provider and hardware domain logic, but move blocking collection and AppKit-dependent decisions out of worker execution. Add one mandatory presentation-safety and firmware-validation boundary, make settings persistence explicitly versioned and lossless, then strengthen the local macOS release gate. The historical controller remains a compatibility host during this wave.

**Tech Stack:** Python 3.10+, PyObjC/AppKit, pytest, Ruff, SidePulse LED DSL, packaged `sdled.wasm`, macOS LaunchServices and signing tools.

## Global Constraints

- Keep bundle identifier `io.sidepulse.app`, LaunchAgent label `io.sidepulse.agentstatus`, state paths, provider hook paths, and TCC-bearing application identity unchanged.
- No provider scan, recursive directory walk, network request, subprocess fork, fsync, or hardware write may run from menu tracking or AppKit draw callbacks.
- No routine main-thread task may perform uncached battery collection.
- Every visible or physical animation must satisfy the same safety compiler, including previews and test signals.
- Exact post-transform LED bytes must pass the packaged firmware parser before production hardware writes.
- Settings written by a newer unsupported schema must never be overwritten by an older writer.
- New behavior is developed test-first and committed in independently reviewable units.
- The complete macOS/PyObjC, physical-hardware, signing, notarization, stapling, installed-upgrade, and performance gate remains mandatory before a release tag.

---

### Task 1: Lossless device settings persistence

**Files:**
- Modify: `src/sidepulse/settings.py`
- Modify: `tests/test_settings.py`
- Create: `tests/test_settings_schema_coverage.py`

**Interfaces:**
- Consumes: `DeviceDisplaySetting.to_dict()`, `_device_display_settings()`, `save_settings()`, `load_settings()`.
- Produces: `DEVICE_SETTING_PERSISTED_FIELDS: frozenset[str]` and lossless persistence for every durable `DeviceDisplaySetting` field.

- [ ] **Step 1: Write the failing resting-glow round-trip test**

```python
from pathlib import Path

from sidepulse.settings import (
    AgentMonitorSettings,
    DeviceDisplaySetting,
    load_settings,
    save_settings,
)


def test_device_resting_glow_survives_unrelated_settings_save(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    configured = AgentMonitorSettings(
        devices=(
            DeviceDisplaySetting(
                device_id="SidePulsePro",
                name="SidePulse Pro",
                path="/Volumes/SidePulsePro",
                resting_glow=0.17,
            ),
        ),
    )
    save_settings(configured, target)
    restored = load_settings(target).with_tips_enabled(False)
    save_settings(restored, target)

    reloaded = load_settings(target)

    assert reloaded.devices[0].resting_glow == 0.17
```

- [ ] **Step 2: Run the focused test and confirm the current encoder loses the value**

Run: `python -m pytest tests/test_settings.py::test_device_resting_glow_survives_unrelated_settings_save -q`

Expected: FAIL because `resting_glow` reloads as `0.0`.

- [ ] **Step 3: Serialize `resting_glow` in `DeviceDisplaySetting.to_dict()`**

```python
"resting_glow": max(0.0, min(0.35, float(self.resting_glow))),
```

Keep the field beside brightness and calibration fields. Do not alter its runtime clamp.

- [ ] **Step 4: Add a persisted-field manifest and schema coverage test**

In `settings.py`:

```python
DEVICE_SETTING_PERSISTED_FIELDS = frozenset(
    {
        "id",
        "name",
        "path",
        "led_display",
        "brightness",
        "auto_brightness_enabled",
        "red_gain",
        "green_gain",
        "blue_gain",
        "resting_glow",
        "blend_mode",
        "provider_pin",
        "signal_policy",
    }
)
```

In `tests/test_settings_schema_coverage.py`:

```python
from sidepulse.settings import (
    DEVICE_SETTING_PERSISTED_FIELDS,
    DeviceDisplaySetting,
)


def test_device_setting_encoder_covers_the_durable_schema() -> None:
    payload = DeviceDisplaySetting("id", "name", "/tmp/device").to_dict()
    assert set(payload) == DEVICE_SETTING_PERSISTED_FIELDS
```

- [ ] **Step 5: Run settings tests**

Run: `python -m pytest tests/test_settings.py tests/test_settings_schema_coverage.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sidepulse/settings.py tests/test_settings.py tests/test_settings_schema_coverage.py
git commit -m "fix: preserve all device settings across saves"
```

---

### Task 2: Versioned, downgrade-safe settings envelope

**Files:**
- Modify: `src/sidepulse/settings.py`
- Modify: `tests/test_settings.py`
- Create: `tests/test_settings_compatibility.py`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**
- Produces: `CURRENT_SETTINGS_SCHEMA_VERSION`, `MIN_READABLE_SETTINGS_SCHEMA_VERSION`, `MIN_WRITABLE_SETTINGS_SCHEMA_VERSION`, `SettingsCompatibility`, `load_settings_document(path)`, and `save_settings(..., compatibility=...)`.
- Compatibility rule: schema `1` migrates to schema `2`; schema greater than `2` is readable only as an explicit read-only compatibility result and is never overwritten.

- [ ] **Step 1: Write the failing newer-schema no-overwrite test**

```python
import json
from pathlib import Path

import pytest

from sidepulse.settings import (
    SettingsWriteRefusedError,
    load_settings_document,
    save_settings,
)


def test_newer_settings_schema_is_read_only_and_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    original = {
        "settings_schema_version": 999,
        "future_feature": {"preserve": True},
        "tips_enabled": False,
    }
    target.write_text(json.dumps(original), encoding="utf-8")

    loaded = load_settings_document(target)

    assert loaded.compatibility.read_only is True
    with pytest.raises(SettingsWriteRefusedError):
        save_settings(loaded.settings, target, compatibility=loaded.compatibility)
    assert json.loads(target.read_text(encoding="utf-8")) == original
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_settings_compatibility.py::test_newer_settings_schema_is_read_only_and_never_overwritten -q`

Expected: FAIL because the compatibility API does not exist.

- [ ] **Step 3: Add explicit schema constants and compatibility types**

```python
CURRENT_SETTINGS_SCHEMA_VERSION = 2
MIN_READABLE_SETTINGS_SCHEMA_VERSION = 1
MIN_WRITABLE_SETTINGS_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class SettingsCompatibility:
    source_version: int
    target_version: int
    read_only: bool
    migrated: bool


@dataclass(frozen=True, slots=True)
class LoadedSettings:
    settings: AgentMonitorSettings
    compatibility: SettingsCompatibility


class SettingsWriteRefusedError(RuntimeError):
    pass
```

Keep `SETTINGS_SCHEMA_VERSION` as a compatibility alias to `CURRENT_SETTINGS_SCHEMA_VERSION` until all callers migrate.

- [ ] **Step 4: Implement strict version parsing**

```python
def _settings_schema_version(data: dict[str, object]) -> int:
    raw = data.get("settings_schema_version", 1)
    if type(raw) is not int or raw < 1:
        raise ValueError("invalid settings schema version")
    return raw
```

A malformed version preserves the file as corrupt. A newer version returns defaults plus `read_only=True`; it does not move or rewrite the source file.

- [ ] **Step 5: Implement the single ordered migration from schema 1 to 2**

```python
def _migrate_settings_document(data: dict[str, object], source_version: int) -> dict[str, object]:
    migrated = dict(data)
    version = source_version
    while version < CURRENT_SETTINGS_SCHEMA_VERSION:
        if version == 1:
            migrated["settings_schema_version"] = 2
            version = 2
            continue
        raise ValueError("unsupported settings migration")
    return migrated
```

The migration is intentionally structural-only in this wave. It gives future changes a real version boundary without changing current user values.

- [ ] **Step 6: Split load into a document API and preserve the existing convenience API**

```python
def load_settings_document(path: Path | None = None) -> LoadedSettings:
    ...


def load_settings(path: Path | None = None) -> AgentMonitorSettings:
    return load_settings_document(path).settings
```

- [ ] **Step 7: Refuse unsafe writes**

Extend `save_settings` with a keyword-only compatibility parameter:

```python
def save_settings(
    settings: AgentMonitorSettings,
    path: Path | None = None,
    *,
    compatibility: SettingsCompatibility | None = None,
) -> Path:
    if compatibility is not None and compatibility.read_only:
        raise SettingsWriteRefusedError("settings were written by a newer SidePulse version")
    ...
```

All ordinary current-version callers remain source-compatible.

- [ ] **Step 8: Add migration and idempotence tests**

```python
def test_schema_one_migrates_to_two_without_changing_user_values(tmp_path: Path) -> None:
    ...


def test_schema_two_round_trip_is_idempotent(tmp_path: Path) -> None:
    ...


def test_invalid_schema_version_preserves_corrupt_source(tmp_path: Path) -> None:
    ...
```

Each test must assert the exact source and output JSON fields.

- [ ] **Step 9: Run settings compatibility suite**

Run: `python -m pytest tests/test_settings.py tests/test_settings_schema_coverage.py tests/test_settings_compatibility.py -q`

Expected: PASS.

- [ ] **Step 10: Document downgrade behavior and commit**

```bash
git add src/sidepulse/settings.py tests/test_settings.py tests/test_settings_schema_coverage.py tests/test_settings_compatibility.py docs/ARCHITECTURE.md
git commit -m "feat: make settings migrations versioned and downgrade safe"
```

---

### Task 3: Asynchronous, bounded battery collection

**Files:**
- Modify: `src/sidepulse/battery.py`
- Modify: `src/sidepulse/status_bar_legacy.py`
- Modify: `src/sidepulse/runtime_scheduler.py`
- Modify: `tests/test_main_thread_is_not_blocked.py`
- Modify: `tests/test_runtime_scheduler.py`
- Create: `tests/test_battery_runtime.py`

**Interfaces:**
- Produces: `BATTERY_READ_TIMEOUT_SECONDS = 2.0`, `BatteryObservationRequest`, `BatteryObservationResult`, runtime work key `battery-observation`, immutable last-known-good battery state, and stale/error metadata.

- [ ] **Step 1: Write the failing subprocess-timeout test**

```python
from unittest.mock import Mock

from sidepulse.battery import BATTERY_READ_TIMEOUT_SECONDS, read_battery_snapshot


def test_battery_reader_has_a_strict_subprocess_timeout() -> None:
    runner = Mock(side_effect=TimeoutError("stalled"))

    try:
        read_battery_snapshot(runner=runner)
    except TimeoutError:
        pass

    assert runner.call_args.kwargs["timeout"] == BATTERY_READ_TIMEOUT_SECONDS
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_battery_runtime.py::test_battery_reader_has_a_strict_subprocess_timeout -q`

Expected: FAIL because no timeout is passed.

- [ ] **Step 3: Add the timeout in `battery.read_battery_snapshot()`**

```python
BATTERY_READ_TIMEOUT_SECONDS = 2.0

result = runner(
    [...],
    check=True,
    capture_output=True,
    timeout=BATTERY_READ_TIMEOUT_SECONDS,
)
```

- [ ] **Step 4: Write the failing main-thread boundary test**

The test should parse `StatusBarController.refresh_` and assert it does not call `read_battery_snapshot`, `_read_battery_snapshot_uncached`, or `subprocess.run`. It should also exercise the controller with a battery worker stub and prove refresh consumes the cached immutable result only.

- [ ] **Step 5: Add battery request/result value objects**

```python
@dataclass(frozen=True, slots=True)
class BatteryObservationRequest:
    full_charge_watts: float | None


@dataclass(frozen=True, slots=True)
class BatteryObservationResult:
    available: bool
    snapshot: BatterySnapshot | None
    reason: str | None
```

Use product-owned reasons: `battery_unavailable`, `battery_timed_out`, `battery_malformed`.

- [ ] **Step 6: Route battery observation through the OS-poll worker**

Add `battery-observation` to `_execute_os_poll_command()`. The worker calls the bounded reader. `_apply_os_poll_result()` installs the immutable result on the main thread only when generation, deadline, runtime, and feature state match.

- [ ] **Step 7: Replace synchronous battery reads in `refresh_()`**

`refresh_()` reads `self._battery_snapshot` and never calls the uncached reader. Battery polling is scheduled when any of these are true:

- physical hardware is enabled;
- the virtual Screen Bar is enabled;
- low-battery alerts are enabled;
- battery display or power-change preview is enabled;
- the Devices pane is visible.

- [ ] **Step 8: Add latest-known-good and coalescing tests**

Tests must prove:

- a timed-out observation preserves the previous snapshot;
- two pending battery requests replace rather than queue;
- stale-generation results are ignored;
- a refresh while the probe is running is nonblocking;
- application termination does not wait indefinitely for the subprocess.

- [ ] **Step 9: Run runtime tests**

Run: `python -m pytest tests/test_battery_runtime.py tests/test_main_thread_is_not_blocked.py tests/test_runtime_scheduler.py -q`

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/sidepulse/battery.py src/sidepulse/status_bar_legacy.py src/sidepulse/runtime_scheduler.py tests/test_battery_runtime.py tests/test_main_thread_is_not_blocked.py tests/test_runtime_scheduler.py
git commit -m "perf: move battery collection off the AppKit thread"
```

---

### Task 4: Immutable hardware render context

**Files:**
- Create: `src/sidepulse/hardware_render.py`
- Modify: `src/sidepulse/status_bar_legacy.py`
- Modify: `tests/test_multi_agent_render_routing.py`
- Create: `tests/test_hardware_render_context.py`

**Interfaces:**
- Produces: `HardwareRenderContext`, `build_hardware_render_context(...)`, and `HardwareWriteRequest.render_context`.
- Worker executors may consume only frozen dataclasses, pure value objects, and worker-owned controllers. They may not read `NSWindow`, `NSView`, `NSMenu`, settings UI maps, or the mutable controller.

- [ ] **Step 1: Write the failing worker isolation test**

```python
class ExplodingWindow:
    def isVisible(self):
        raise AssertionError("worker touched AppKit")


def test_hardware_worker_never_reads_the_settings_window(controller, request) -> None:
    controller.settings_window = ExplodingWindow()
    result = controller._sync_hardware_device(request)
    assert result.write is not None
```

Construct `request` with a precomputed render context. The test initially fails because `agent_render_colors()` reads `settings_window.isVisible()`.

- [ ] **Step 2: Define `HardwareRenderContext`**

```python
@dataclass(frozen=True, slots=True)
class HardwareRenderContext:
    colors: ColorSettings
    preview_uses_baseline: bool
    escalation_stage: int
    presentation_time: float
    accessibility_preferences: AccessibilityDisplayPreferences
```

Include any additional immutable field required by `_sync_hardware_device`; do not pass the controller or AppKit objects.

- [ ] **Step 3: Build the context on the main thread**

Move the Settings-window visibility decision, preview baseline selection, deep-work speed adjustment, and escalation speed adjustment into `build_hardware_render_context()` before work submission.

- [ ] **Step 4: Make `_sync_hardware_device()` consume only the request**

Replace `self.agent_render_colors()` and all other UI reads with `request.render_context.colors` and request-owned values.

- [ ] **Step 5: Add an AST architecture test**

The test should inspect `_execute_hardware_write_command` and `_sync_hardware_device` for calls to `.isVisible`, `settings_fields`, `settings_buttons`, `NSApp`, `NSWindow`, `NSView`, and `current_settings_pane`. Any match fails.

- [ ] **Step 6: Run focused tests**

Run: `python -m pytest tests/test_hardware_render_context.py tests/test_multi_agent_render_routing.py tests/test_led_write_storm.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/sidepulse/hardware_render.py src/sidepulse/status_bar_legacy.py tests/test_hardware_render_context.py tests/test_multi_agent_render_routing.py
git commit -m "refactor: isolate hardware rendering from AppKit state"
```

---

### Task 5: Mandatory presentation safety compiler

**Files:**
- Create: `src/sidepulse/presentation_compiler.py`
- Modify: `src/sidepulse/signals.py`
- Modify: `src/sidepulse/led_status.py`
- Modify: `src/sidepulse/status_bar_legacy.py`
- Modify: `src/sidepulse/virtual_device.py`
- Modify: `tests/test_temporal_safety.py`
- Create: `tests/test_presentation_compiler.py`
- Create: `tests/test_presentation_safety_reachability.py`

**Interfaces:**
- Produces: `PresentationSurface`, `PresentationSafetyPreferences`, `PresentationCompileResult`, `PresentationSafetyError`, and `compile_presentation(program, *, surface, preferences, parser=None)`.
- Every first-party render path must call this compiler before visible playback or physical write.

- [ ] **Step 1: Write failing takeover and preview cadence tests**

```python
def test_escalation_takeover_is_compiled_below_the_flash_ceiling(controller) -> None:
    program = controller.escalation_takeover_program(255)
    assert measured_repeat_hz(program) <= 2.0


def test_fast_preview_is_safely_transformed() -> None:
    raw = style_to_program(SignalStyle("#FFFFFF", "blink", 0.1, 1.0))
    compiled = compile_presentation(raw, surface=PresentationSurface.PREVIEW)
    assert measured_repeat_hz(compiled.program) <= 2.0
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_presentation_compiler.py -q`

Expected: FAIL because no universal compiler exists and takeover exceeds 2 Hz.

- [ ] **Step 3: Define the compiler result and safety policy**

```python
class PresentationSurface(str, Enum):
    HARDWARE = "hardware"
    SCREEN_BAR = "screen_bar"
    PREVIEW = "preview"


@dataclass(frozen=True, slots=True)
class PresentationSafetyPreferences:
    reduce_motion: bool = False
    differentiate_without_color: bool = False
    max_cycle_hz: float = 2.0
    max_saturated_red_hz: float = 1.0


@dataclass(frozen=True, slots=True)
class PresentationCompileResult:
    program: str
    transformed: bool
    reasons: tuple[str, ...]
```

- [ ] **Step 4: Reuse the existing temporal parser instead of regex-only timing guesses**

Use `temporal_safety.py` and the animation parser to derive loop cadence. When cadence is unsafe, deterministically scale every duration and delay in the loop by the minimum factor needed to satisfy the applicable ceiling.

- [ ] **Step 5: Implement saturated-red handling**

If a loop contains a high-saturation red flash, apply `max_saturated_red_hz`. Preserve hue for steady states. Under Reduce Motion, compile repeating motion to a steady semantic color plus a non-color glyph/accessibility cue on screen surfaces.

- [ ] **Step 6: Route every render site through the compiler**

Cover:

- escalation takeover;
- all Signal Engine outputs;
- settings thumbnails;
- settings live previews;
- setup demo;
- test signal;
- Studio preview;
- Screen Bar program installation;
- physical-device final program.

- [ ] **Step 7: Add a static reachability test**

The test enumerates direct calls to `VirtualLedView.setProgram_`, `AgentLedController.sync_program`, and `write_led_program` from first-party presentation modules. Each call must receive a `PresentationCompileResult.program` or be on an explicit allowlist limited to CLI expert mode and parser-tested installer code.

- [ ] **Step 8: Add property-style coverage over legal signal settings**

For every signal pattern, speed endpoint, intensity endpoint, surface, Reduce Motion setting, and representative color, compile and assert:

- no cycle exceeds the applicable ceiling;
- output remains within 512 bytes and 20 lines;
- compiler output is deterministic;
- already-safe output is byte-identical.

- [ ] **Step 9: Run safety suites**

Run: `python -m pytest tests/test_presentation_compiler.py tests/test_presentation_safety_reachability.py tests/test_temporal_safety.py tests/test_interrupt_budget.py tests/test_animation_editor.py -q`

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/sidepulse/presentation_compiler.py src/sidepulse/signals.py src/sidepulse/led_status.py src/sidepulse/status_bar_legacy.py src/sidepulse/virtual_device.py tests/test_presentation_compiler.py tests/test_presentation_safety_reachability.py tests/test_temporal_safety.py
git commit -m "feat: enforce one presentation safety compiler"
```

---

### Task 6: Universal final-byte firmware validation

**Files:**
- Create: `src/sidepulse/firmware_validation.py`
- Modify: `src/sidepulse/device_writer.py`
- Modify: `src/sidepulse/led_status.py`
- Modify: `src/sidepulse/led_wasm.py`
- Modify: `tests/test_device_writer_security.py`
- Create: `tests/test_firmware_validation.py`
- Modify: `tests/test_presentation_parity.py`

**Interfaces:**
- Produces: `FirmwareValidationResult`, `FirmwareProgramValidator`, cached exact-byte validation, product-owned refusal codes, and an injected validator on `write_led_program()`.

- [ ] **Step 1: Write the failing malformed-runtime-program test**

```python
import pytest

from sidepulse.device_writer import DeviceWriteError, write_led_program


def test_runtime_writer_rejects_firmware_invalid_program_before_opening_device(tmp_path) -> None:
    target = tmp_path / "SidePulsePro"
    target.mkdir()

    with pytest.raises(DeviceWriteError, match="firmware rejected"):
        write_led_program("0:off", device_path=target)

    assert not (target / "LEDS.LED").exists()
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_firmware_validation.py::test_runtime_writer_rejects_firmware_invalid_program_before_opening_device -q`

Expected: FAIL because only size and line count are checked.

- [ ] **Step 3: Implement a reusable exact-byte validator**

```python
@dataclass(frozen=True, slots=True)
class FirmwareValidationResult:
    accepted: bool
    reason: str | None


class FirmwareProgramValidator:
    def validate(self, program: str, *, led_count: int) -> FirmwareValidationResult:
        ...
```

Use `SdLedWasmController.parse()` and cache by `(sha256(program_bytes), led_count, parser_version)` with a bounded LRU.

- [ ] **Step 4: Inject validation into `write_led_program()`**

Add keyword-only parameters:

```python
validator: FirmwareProgramValidator | None = None,
led_count: int | None = None,
require_firmware_validation: bool = True,
```

Production default requires validation. Tests and controlled expert tooling may inject a deterministic fake. The production path must never silently skip because the parser is unavailable.

- [ ] **Step 5: Validate after every textual transform**

`AgentLedController._for_strip()` returns transformed text. Validation occurs on that exact text immediately before any target file is opened or modified.

- [ ] **Step 6: Preserve last-known-good output on rejection**

`AgentLedController` records a product-owned error and leaves the existing device file untouched. Do not write a malformed program and do not replace a working animation with the firmware’s error strobe.

- [ ] **Step 7: Add the complete renderer matrix test**

Generate all first-party lifecycle, signal, battery, timer, quota, blend, device-size, brightness, channel-gain, resting-glow, Reduce Motion, and finite-cue outputs. Compile them, apply final transforms, and validate exact bytes against the packaged parser.

- [ ] **Step 8: Run writer and parity tests**

Run: `python -m pytest tests/test_firmware_validation.py tests/test_device_writer_security.py tests/test_presentation_parity.py tests/test_surface_transfer.py -q`

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/sidepulse/firmware_validation.py src/sidepulse/device_writer.py src/sidepulse/led_status.py src/sidepulse/led_wasm.py tests/test_firmware_validation.py tests/test_device_writer_security.py tests/test_presentation_parity.py
git commit -m "feat: validate exact LED bytes with the firmware parser"
```

---

### Task 7: Authoritative local macOS release gate

**Files:**
- Create: `scripts/verify_macos_release.sh`
- Create: `scripts/verify_installed_upgrade.py`
- Create: `scripts/verify_performance_budget.py`
- Modify: `scripts/verify.sh`
- Modify: `scripts/publish_release.sh`
- Modify: `packaging/build_macos_pkg.sh`
- Modify: `docs/LOCAL-VERIFICATION.md`
- Modify: `tests/test_build_script_contract.py`
- Modify: `tests/test_packaging_contract.py`

**Interfaces:**
- Produces one release command that verifies source, package, installed upgrade, identity, Gatekeeper, notarization, physical-device smoke tests, and performance evidence before tag creation.

- [ ] **Step 1: Write failing script-contract tests**

Require `verify_macos_release.sh` to contain and check:

- `codesign --verify --deep --strict`;
- `spctl --assess --type execute`;
- `pkgutil --check-signature`;
- `stapler validate`;
- installed version and bundle identifier;
- clean upgrade from the previous installed version;
- launch and status-item smoke result;
- no untracked build inputs;
- local `main` equals `origin/main`;
- performance-budget result;
- explicit physical-device opt-in evidence.

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_build_script_contract.py tests/test_packaging_contract.py -q`

Expected: FAIL because the release gate script does not exist.

- [ ] **Step 3: Implement `verify_macos_release.sh`**

The script must fail unless running on macOS, on clean `main`, with no untracked files outside ignored paths, and with local `HEAD` equal to `origin/main`. It calls:

```bash
./scripts/verify.sh --no-bootstrap
SIDEPULSE_VERIFY_MACOS_PACKAGE=1 ./scripts/verify.sh --no-bootstrap
python scripts/verify_installed_upgrade.py dist/SidePulse-*.pkg
python scripts/verify_performance_budget.py --capture "$SIDEPULSE_PERF_CAPTURE"
```

- [ ] **Step 4: Implement installed-upgrade verification**

The verifier records current bundle ID, TeamIdentifier, settings fixture, LaunchAgent, hooks, and TCC-sensitive paths; installs the candidate; relaunches; verifies identity and preserved settings; checks hooks and LaunchAgent; then emits a machine-readable JSON receipt.

- [ ] **Step 5: Implement performance-budget verification**

Parse an Instruments or `sample`/`powermetrics` capture and enforce the approved thresholds:

- hidden idle median CPU below 1%;
- static Screen Bar below 1.5%;
- gentle motion below 3%;
- menu open P95 below 50 ms;
- settings switch P95 below 100 ms;
- no routine main-thread span above 16 ms.

A missing capture is a release failure, not a warning.

- [ ] **Step 6: Make publication transactional**

`publish_release.sh` must run the release gate before creating a tag. It verifies artifact hashes and GitHub authentication first, creates a draft release, uploads all artifacts, publishes the release, then pushes or creates the final tag in an order that cannot leave an advertised release without artifacts. Any partial draft is deleted on failure.

- [ ] **Step 7: Run portable contract tests**

Run: `python -m pytest tests/test_build_script_contract.py tests/test_packaging_contract.py tests/test_workflow_contract.py -q`

Expected: PASS.

- [ ] **Step 8: Run shell syntax checks**

Run: `bash -n scripts/verify.sh scripts/verify_macos_release.sh scripts/publish_release.sh packaging/build_macos_pkg.sh`

Expected: exit 0.

- [ ] **Step 9: Commit**

```bash
git add scripts/verify_macos_release.sh scripts/verify_installed_upgrade.py scripts/verify_performance_budget.py scripts/verify.sh scripts/publish_release.sh packaging/build_macos_pkg.sh docs/LOCAL-VERIFICATION.md tests/test_build_script_contract.py tests/test_packaging_contract.py
git commit -m "build: require the complete local macOS release gate"
```

---

### Task 8: Wave verification and pull request

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/WAVE-STATUS.md`
- Modify: `docs/audits/2026-08-15-sidepulse-deep-production-audit.md`

**Interfaces:**
- Produces the release-blocker wave evidence and explicitly records any Mac-only gate still awaiting the owner machine.

- [ ] **Step 1: Run the complete portable gate**

Run: `./scripts/verify.sh --portable`

Expected: lint, compile, focused tests, build, Twine, and clean-install checks pass.

- [ ] **Step 2: Run all pure tests touched by this wave**

Run:

```bash
python -m pytest \
  tests/test_settings.py \
  tests/test_settings_schema_coverage.py \
  tests/test_settings_compatibility.py \
  tests/test_battery_runtime.py \
  tests/test_main_thread_is_not_blocked.py \
  tests/test_runtime_scheduler.py \
  tests/test_hardware_render_context.py \
  tests/test_multi_agent_render_routing.py \
  tests/test_presentation_compiler.py \
  tests/test_presentation_safety_reachability.py \
  tests/test_temporal_safety.py \
  tests/test_firmware_validation.py \
  tests/test_device_writer_security.py \
  tests/test_presentation_parity.py \
  tests/test_build_script_contract.py \
  tests/test_packaging_contract.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Update evidence documents**

Mark SP-AUD-001 through SP-AUD-006 fixed only with test evidence. Keep SP-AUD-007 open until `verify_macos_release.sh` succeeds on the owner’s Mac with signed artifacts and physical hardware.

- [ ] **Step 4: Review the complete diff**

Run: `git diff main...HEAD --check` and inspect every changed file for secrets, generated artifacts, debug prints, and bypass flags.

- [ ] **Step 5: Commit documentation**

```bash
git add CHANGELOG.md docs/WAVE-STATUS.md docs/audits/2026-08-15-sidepulse-deep-production-audit.md
git commit -m "docs: record production blocker remediation evidence"
```

- [ ] **Step 6: Open a pull request**

Title: `Fix SidePulse production release blockers`

The body must list each blocker, exact tests run, portable verification output, and the remaining owner-Mac release gate. Do not mark the application production-ready until that final gate succeeds.
