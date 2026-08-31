# JR Bar Private Phone Glance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an end-to-end, signed, read-only JR Bar phone glance over an explicitly configured private-network address.

**Architecture:** A glance-only Python listener keeps the general status API on loopback. The network envelope carries the exact signed bytes for deterministic Swift verification. The iOS app validates, fetches, verifies, replay-checks, persists, and presents the glance only while foregrounded or on manual refresh.

**Tech Stack:** Python 3.10+, `http.server`, HMAC-SHA256, Swift 6, Foundation, CryptoKit, Security, SwiftUI, UIKit.

**Spec:** `docs/superpowers/specs/2026-08-31-jr-bar-private-phone-glance-design.md`

## Global Constraints

- Keep `sidepulse serve` bound to `127.0.0.1` and keep `/status.json` off the LAN listener.
- Add no production dependency.
- Accept only explicit private or link-local IP literals, never wildcards, hostnames, loopback, or public addresses.
- Keep the network response at or below 8 KiB and never log or persist the HMAC secret.
- The iOS client is read-only and refreshes only in the foreground or manually.
- Do not commit, push, publish, deploy, change permissions, or modify credentials.

---

### Task 1: Isolated private-network listener

**Files:**
- Create: `src/sidepulse/glance_server.py`
- Modify: `src/sidepulse/cli.py`
- Test: `tests/test_serve.py`

**Interfaces:**
- Consumes: `build_phone_glance_projection()` and `encode_phone_glance()` from the existing Python contract.
- Produces: `validate_bind_address(value: str) -> str`, `create_glance_server(...) -> ThreadingHTTPServer`, and `glance_serve(...) -> None`.

- [ ] Add failing tests that reject non-private addresses and prove only `GET /glance.json` succeeds.
- [ ] Run only the new tests and confirm they fail for the missing listener.
- [ ] Implement the glance-only handler, explicit bind validator, and `sidepulse glance` parser/dispatcher.
- [ ] Run `tests/test_serve.py` once and run targeted Ruff on the changed Python files.

### Task 2: Deterministic signed-body transport

**Files:**
- Modify: `src/sidepulse/phone_glance.py`
- Modify: `src/sidepulse/serve.py`
- Test: `tests/test_phone_glance.py`
- Test: `tests/test_serve.py`

**Interfaces:**
- Consumes: the current canonical `_unsigned()` Python bytes.
- Produces: optional `PhoneGlanceEnvelope.signed_body: str | None`; network encoding always includes a base64url signed body; verification rejects any mismatch between signed and readable fields.

- [ ] Add failing tests for network encoding without reconstructed JSON, tampered readable fields, tampered signed body, and the 8 KiB cap.
- [ ] Run only those tests and confirm the missing `signed_body` behavior fails.
- [ ] Add bounded base64url encoding and decoding, sign and verify the decoded bytes, and retain local in-memory compatibility.
- [ ] Run `tests/test_phone_glance.py tests/test_serve.py` once and run targeted Ruff.

### Task 3: Pure Swift verifier and network client

**Files:**
- Create: `ios/SidePulse/SidePulse/PhoneGlanceContract.swift`
- Create: `ios/SidePulse/SidePulse/PhoneGlanceClient.swift`
- Modify: `ios/SidePulse/SidePulse.xcodeproj/project.pbxproj`
- Create: `tests/test_ios_phone_glance_contract.py`

**Interfaces:**
- Produces: `PhoneGlanceEndpoint`, `VerifiedPhoneGlance`, `PhoneGlanceContract.verify(data:secret:lastSequence:now:)`, and `PhoneGlanceClient.fetch(endpoint:secret:lastSequence:now:)`.
- Guarantees: five-second timeout, no redirects, 8 KiB cap, strict HMAC, age, skew, signed-body equality, and sequence checks.

- [ ] Add a Python test that creates a signed fixture and compiles a Swift harness against `PhoneGlanceContract.swift`.
- [ ] Run the new test and confirm the production Swift types are missing.
- [ ] Implement strict endpoint and envelope verification, then the ephemeral URLSession client.
- [ ] Add both Swift sources to the application target.
- [ ] Run the focused Python-to-Swift test and `xcrun swiftc -typecheck` for the pure Swift files.

### Task 4: Protected configuration and replay persistence

**Files:**
- Modify: `ios/SidePulse/SidePulse/KeychainStore.swift`
- Modify: `ios/SidePulse/SidePulse/AppModel.swift`
- Modify: `ios/SidePulse/SidePulse/Info.plist`

**Interfaces:**
- Consumes: Task 3's endpoint, snapshot, verifier, and client types.
- Produces: saved `phoneGlanceHost`, `phoneGlancePort`, dedicated protected secret, `PhoneGlanceLoadState`, `savePhoneGlanceConfiguration(...)`, and `refreshPhoneGlance()`.

- [ ] Add a dedicated Keychain key for the glance secret, separate from the APNs proxy secret.
- [ ] Add bounded host and port persistence plus a sequence checkpoint keyed by verified source ID.
- [ ] Advance the sequence only after a complete verified response and expose generic failure state.
- [ ] Add the local-network purpose string and local-network HTTP exception to `Info.plist`.
- [ ] Typecheck the Swift source batch without running an Xcode build.

### Task 5: Foreground status card and settings

**Files:**
- Modify: `ios/SidePulse/SidePulse/ContentView.swift`
- Modify: `ios/SidePulse/README.md`

**Interfaces:**
- Consumes: Task 4's configuration fields, load state, save action, and async refresh action.
- Produces: a main-screen Computer Glance card and a Phone Glance settings section.

- [ ] Add honest not-configured, checking, verified, stale, and unavailable states with accessible labels.
- [ ] Add private-IP, port, and SecureField configuration plus Save and Test controls.
- [ ] Refresh on app activation and manual action only, with no timer or background mode.
- [ ] Document the exact Mac command, secret handling, LAN-only scope, and lack of transport encryption.
- [ ] Typecheck the changed Swift source where the local toolchain permits and inspect the source UI structure.

### Task 6: Batch checkpoint

**Files:**
- Modify: `docs/ROADMAP.md`
- Modify: `docs/superpowers/plans/2026-08-28-jr-bar-completion-contract.md`

**Interfaces:**
- Consumes: all Task 1 through Task 5 receipts.
- Produces: a truthful source-complete item 46 record with external device and LAN verification kept separate.

- [ ] Run one combined focused Python gate for phone-glance, serve, CLI reachability, and Swift interoperability.
- [ ] Run targeted Ruff and `git diff --check` once for the batch.
- [ ] Update the roadmap and completion contract with exact commands and limitations.
- [ ] Defer `make fast` until the next major source checkpoint and defer the full suite until final source freeze.

## Self-review

- Every design requirement maps to a task.
- All cross-task types and method names are defined before consumption.
- No placeholder implementation, public-network access, remote command, dependency, or background polling remains in scope.
- Commit steps are intentionally omitted because this repository has no commit authority in the current task.
