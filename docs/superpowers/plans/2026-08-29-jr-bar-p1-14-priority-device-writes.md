# JR Bar P1.14 Priority-Aware Latest-Wins Device Writes Plan

**Status:** Complete (source-verified)

**Scope:** Preserve the existing long-lived hardware worker and final firmware
write boundary, but separate protected physical-light commands from the latest
coalescible state for each device. Remove calibration and lid-preview races with
already queued worker writes.

## Source observations

- `sync_leds()` already submits one `HardwareWriteRequest` per physical device
  to one retained `LatestWinsWorker`; no per-refresh hardware thread is created.
- The worker currently keys pending work only by device. Any newer refresh for
  that device replaces the pending request, regardless of whether the older
  request represents an ask, failure, finite cue, or explicit preview.
- One pending slot cannot retain both a protected cue and the latest state that
  must follow it. The presentation layer is priority-aware, but the write queue
  discards that information.
- `HardwareWriteRequest` does not snapshot the selected display kind. The worker
  recomputes it later from mutable controller state, so a delayed request can
  render a different signal than the one that was submitted.
- Calibration writes call the device controller synchronously outside the
  hardware worker. A previously queued write can overwrite the reference color.
- Lid flourishes write from a detached thread. New refresh submissions are
  suppressed while the animation is active, but an older running or pending
  worker command can still race the flourish.
- `device_writer.write_led_program()` remains the required final safety gate.
  Firmware parsing, byte and line bounds, inode safety, fsync, readback, and
  atomicity must not be duplicated or bypassed.

## Design

1. Extend `RuntimeWorkCommand` with a validated priority and optional normalized
   coalescing key. Default values preserve every existing worker domain.
2. Make `LatestWinsWorker` index pending work by coalescing key and select the
   highest-priority command first, then the existing deadline and stable-key
   order. Repeated commands in the same semantic slot remain latest-wins.
3. Give hardware requests a snapshotted display kind, write priority, and stable
   semantic coalescing identity:
   - ordinary ambient and lifecycle refreshes use one `latest` slot per device;
   - persistent asks and failures use protected semantic slots;
   - finite cue event keys use protected event slots;
   - explicit previews use the highest protected preview slot.
4. Use the snapshotted display kind during worker rendering. Mutable brightness,
   calibration, and settings may still be read at the actual write boundary, but
   the submitted semantic signal must not silently change while queued.
5. Add an explicit-program variant to `HardwareWriteRequest` and route physical
   calibration patches through the worker. While one device is under
   calibration, suppress ordinary submissions for that device; closing the
   popover submits the final live state.
6. Before a direct lid flourish starts, advance the hardware generation, cancel
   older pending work, and wait a bounded interval for any running hardware
   command to finish. If the worker cannot become idle, refuse the flourish and
   restore live presentation rather than race two writers.
7. Preserve device-inventory generation cancellation, current queue bounds,
   result dispatch, controller dedupe, Screen Bar synchronization, and the final
   `device_writer` safety compiler and firmware validator.

## Test-first acceptance contract

1. Default worker commands retain the existing same-key latest-wins behavior.
2. Different coalescing slots for one device remain pending together.
3. A protected ask or failure executes before a later ordinary state, and the
   ordinary state executes afterward as the final trailing edge.
4. Repeated copies of the same protected semantic identity coalesce to the
   latest request and do not grow the queue.
5. Priority never bypasses deadline expiry, generation cancellation, global
   pending bounds, or close behavior.
6. Hardware submissions snapshot display kind and generate content-free bounded
   coalescing keys that contain no device path, provider payload, or session text.
7. A physical calibration patch is submitted as explicit worker work; ordinary
   refreshes cannot overwrite it while the calibration is open; closing returns
   the device to the latest live state.
8. A lid flourish cancels older pending work, waits for an older running write,
   writes only after worker idle, and restores the final current state.
9. Existing physical-write security, firmware-boundary, motion-dedupe,
   accessibility, inventory-generation, Screen Bar anchor, and worker-lifecycle
   tests remain green.
10. Focused tests, authoritative Ruff and compilation checks, the canonical
    gate, and one completed independent review have no unresolved correctness,
    safety, data-loss, queue-growth, or threading finding.

## Non-goals and proof boundary

- No new device protocol, firmware grammar, production dependency, animation
  DSL, effect catalog, CLI scheduling layer, or physical frame streamer.
- `INIT.LED` power-up burns and standalone CLI writes remain explicit direct
  transactions outside the app runtime worker.
- This tranche does not claim physical hardware timing, USB-volume durability,
  signed installed-app behavior, or release readiness. Those require later
  installed-app and hardware evidence.

## Receipts

- Implementation:
  - `hardware_write_policy.py` now assigns bounded, content-free semantic slots
    and priorities to ordinary state, persistent attention, failures, finite
    cues, signal tests, routine courtesy signals, and explicit previews.
  - `LatestWinsWorker` now preserves multiple semantic slots, selects by
    priority then deadline and stable key, refuses lower-priority replacement,
    admits protected work by deterministic lower-priority eviction at the
    32-slot bound, and retains protected plus final trailing receipts under
    result-mailbox saturation.
  - Hardware requests snapshot display kind and semantic write policy on the
    main thread. Physical calibration uses the same worker, retains its exact
    preview key across inventory churn, and restores live state on close.
  - Lid flourishes cancel older generations and require the hardware worker to
    become idle before a direct write. A timeout refuses the flourish and
    restores current presentation. The final firmware compiler and device
    writer boundary remain unchanged.
- Focused verification:
  - Ruff, Python compilation, and the focused scheduler, policy, status-bar,
    and architecture suite passed with 869 tests plus 7 subtests.
- Canonical verification:
  - `./scripts/verify.sh --no-bootstrap` passed dependency policy, the 571-file
    tracked secret scan, Ruff, version and compilation checks, 6,362 tests plus
    7 subtests, source and wheel builds, Twine validation, clean-install
    verification, and SBOM generation.
  - The four warnings were the existing multiprocessing `fork()` warnings in
    hook-deduplication tests.
- Independent review:
  - The first review found result-mailbox trailing-state loss, calibration-close
    dependence on rediscovery, and a global-clock wait seam. All three were
    fixed. The follow-up review reported no unresolved finding.
- Proof boundary:
  - No installed-app responsiveness, physical LED timing or USB durability,
    signing, notarization, installation, release, or publication claim is made.
