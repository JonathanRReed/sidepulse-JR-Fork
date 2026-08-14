# SidePulse Installed Agent Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SidePulse discover and monitor installed interactive coding-agent surfaces, beginning with OpenCode and then Google Antigravity and Gemini, without treating installation, process presence, windows, or cloud activity as lifecycle truth.

**Architecture:** Existing `ProviderSpec`, `ProviderSourceRegistration`, provider contracts, canonical hook minimization, and collector sources remain the lifecycle authority. A new pure installed-surface registry describes host presence and support, but cannot emit provider facts. OpenCode uses its official local plugin event contract and keeps capacity delegated to its configured upstream provider. Google surfaces enter lifecycle only where an installed, official hook contract is verified.

**Tech Stack:** Python 3.11, dataclasses and enums, existing private I/O helpers, existing hook JSONL and typed refresh hints, OpenCode's documented local JavaScript plugin contract, pytest, Ruff.

## Global Constraints

- Preserve canonical `SourceKey`, `WorkKey`, `RequestKey`, and `QuotaLaneKey` identities.
- Installation and inventory produce zero lifecycle, request, completion, notification, or capacity facts.
- Do not add production dependencies.
- Do not execute discovered binaries during ordinary inventory. Bounded version probing is a separate opt-in diagnostic.
- Do not read browser cookies, provider credentials, prompt text, file content, raw tool arguments, or private provider endpoints.
- Keep OpenCode capacity attributed to its exact configured upstream provider and account pool. OpenCode itself has no fabricated quota source.
- Preserve all existing provider hook paths and behavior.
- Do not commit, push, install into the user profile, request permissions, or deploy. Record source receipts in task reports instead of committing.

---

## Task 1: Register OpenCode as a first-party lifecycle provider

**Files:**

- Modify: `src/sidepulse/providers.py`
- Modify: `src/sidepulse/provider_contracts.py`
- Modify: `src/sidepulse/provider_adapters.py`
- Modify: `src/sidepulse/provider_facts.py`
- Modify: `src/sidepulse/collector.py`
- Modify: `src/sidepulse/agent_browser.py`
- Modify: `src/sidepulse/interruption_policy.py`
- Modify: `src/sidepulse/operator_accessibility.py`
- Test: `tests/test_provider_registry.py`
- Test: `tests/test_provider_adapters.py`
- Test: focused provider assertions in `tests/test_sidepulse.py`

- [ ] Add literal tests that require `opencode` after `openclaw` in the provider registry and require exactly one `hooks/global/live_agent_events` source plus one `hooks/global/actionable_requests` source.
- [ ] Add literal conformance rows for only the canonical OpenCode events the official plugin bridge emits: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `Notification`, `PreCompact`, `PostCompact`, `Stop`, `StopFailure`, and `SessionEnd`.
- [ ] Run the tests and capture the expected missing-provider RED.
- [ ] Add `OPENCODE_EVENTS`, a `ProviderSpec("opencode", "OpenCode", ...)`, the exact source registration, and the first-party adapter allowlist entry.
- [ ] Add OpenCode to each product-owned provider label table so canonical projections never fall back to generic `Provider` copy.
- [ ] Keep `KNOWN_EVENTS` canonical. Native OpenCode event names remain inside the installed plugin bridge and do not leak into canonical provider adapters.
- [ ] Run focused provider registry, adapter, facts, collector, agent-browser, interruption, and accessibility tests.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 2: Add the official OpenCode plugin detector and installer

**Files:**

- Modify: `src/sidepulse/providers.py`
- Modify: `src/sidepulse/install.py`
- Modify: `src/sidepulse/cli.py` only if the existing `HOOK_PROVIDERS` expansion is insufficient
- Test: `tests/test_sidepulse.py`
- Test: `tests/test_ipc_ownership.py` and private-I/O neighbors as applicable

- [ ] Add failing tests for the default global plugin path `~/.config/opencode/plugins/sidepulse.js`, absent and configured detection, unrelated plugin refusal, exact managed-source detection, dry run, idempotent install, safe replacement, uninstall, link refusal, parent replacement refusal, 0600 file mode, and preservation of unrelated OpenCode configuration.
- [ ] Add a failing behavior test that executes the generated plugin against a fake `Bun.spawn` boundary and asserts content-free canonical payloads for session, permission, question, tool, compact, idle, and error events.
- [ ] Run the tests and capture the expected missing installer/detector RED.
- [ ] Generate one dependency-free global JavaScript plugin. It may forward only product-owned canonical event name, opaque session/work/request identifiers, bounded sequence or timestamp, and a product-owned notification kind. It must not forward prompts, messages, paths, model responses, tool arguments, provider errors, or credentials.
- [ ] Map `session.created` to `SessionStart`, active or busy `session.status` to `UserPromptSubmit`, `session.idle` to `Stop`, `session.error` to `StopFailure`, `permission.asked` to `PermissionRequest`, `permission.replied` to `PostToolUse`, `question.asked` to `Notification` with `input_required`, `question.replied` and `question.rejected` to `PostToolUse`, `tool.execute.before` to `PreToolUse`, `tool.execute.after` to `PostToolUse`, and session compaction events to the matching compact event.
- [ ] Spawn the existing frozen SidePulse hook entry using an argument array, not a shell command string, and send one bounded JSON object on stdin.
- [ ] Install through existing private atomic I/O helpers and uninstall only the exact SidePulse-owned plugin file.
- [ ] Make detection require the exact managed marker and verified provider/log arguments. Presence of an arbitrary `sidepulse.js` must fail closed.
- [ ] Run installer, hook ingress, private-I/O, provider registry, and CLI parser tests.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 3: Add the pure installed-surface registry

**Files:**

- Create: `src/sidepulse/installed_agents.py`
- Create: `tests/test_installed_agents.py`

- [ ] Add strict missing-module tests for `InstalledSurfaceKind`, `SurfaceSupportLevel`, `SurfacePresence`, `InstalledSurfaceKey`, `InstalledSurfaceRegistration`, and `InstalledSurfaceObservation`.
- [ ] Define an immutable literal table for Codex, Claude, Devin, Grok, Cursor, Hermes, OpenClaw, OpenCode CLI, OpenCode desktop, Google Antigravity CLI, Antigravity desktop, Antigravity IDE, Gemini CLI, Gemini Code Assist for VS Code, and GitHub Copilot IDE. Defer Gemini Code Assist for IntelliJ until a bounded, reviewed macOS marker exists.
- [ ] Reject duplicate keys, unbounded labels, executable detector declarations, path traversal, raw paths in observations, and unsupported detector kinds.
- [ ] Implement pure evidence reduction from caller-supplied path and bundle observations. The module must perform no filesystem, process, network, AppKit, or provider work at import time or reduction time.
- [ ] Prove installed-only observations create no canonical provider facts or consumer-visible lifecycle rows.
- [ ] Run the pure registry and canonical invariant neighbors.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 4: Add bounded host inventory collection

**Files:**

- Create: `src/sidepulse/installed_agent_inventory.py`
- Create: `tests/test_installed_agent_inventory.py`
- Modify: `src/sidepulse/runtime_scheduler.py` only if an existing reviewed worker domain cannot carry inventory
- Modify: `src/sidepulse/status_bar.py` in an isolated inventory-owned range

- [ ] Add failing filesystem fixtures for ordinary files, app bundles, IDE extension directories, missing paths, symlinks, parent swaps, ownership mismatch, world-writable parents, output growth, and more than 64 candidates.
- [ ] Collect only reviewed literal paths and bundle identifiers. Do not search the full disk or execute candidates.
- [ ] Run inventory on an existing long-lived bounded worker and apply immutable generation-fenced results on main.
- [ ] Reconcile at launch, explicit refresh, and settings-pane visibility. Hidden or terminated UI must not schedule repeating inventory work.
- [ ] Prove 100 refresh requests retain one running and one latest pending job and never grow an unbounded result mailbox.
- [ ] Run runtime scheduler, private-I/O, status controller, and installed registry tests.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 5: Add Google lifecycle adapters only for verified official hooks

**Files:**

- Modify: `src/sidepulse/providers.py`
- Modify: `src/sidepulse/provider_contracts.py`
- Modify: `src/sidepulse/provider_adapters.py`
- Modify: `src/sidepulse/provider_facts.py`
- Modify: `src/sidepulse/install.py` only for verified supported hook surfaces
- Test: `tests/test_provider_registry.py`
- Test: `tests/test_provider_adapters.py`
- Test: new Google-specific fixtures under `tests/`

- [ ] Freeze exact public hook fixtures for Gemini CLI and the verified Antigravity CLI contract before production changes.
- [ ] Capture missing-provider REDs for exact surface-specific source keys.
- [ ] Register separate lifecycle identities for `gemini-cli` and `antigravity` only where the official installed contract supplies events. Desktop and IDE surfaces remain inventory-only until separately proven.
- [ ] Normalize only documented terminal, permission, tool, prompt, compact, and session events. Unsupported events remain inert and partial.
- [ ] Keep quota or resource exhaustion as a typed terminal cause, never a green completion and never an actionable request without a real request identifier.
- [ ] Preserve content-free persistence and provider-specific source isolation.
- [ ] Run provider adapters, canonical collector, completion, interruption, mailbox, IPC ownership, and freshness gates.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 6: Add the Installed Agents settings projection

**Files:**

- Modify: `src/sidepulse/settings_window.py`
- Modify: `src/sidepulse/status_bar.py` in an isolated installed-agents range
- Modify: `src/sidepulse/operator_accessibility.py`
- Test: focused rendered AppKit tests in `tests/test_sidepulse.py`
- Test: `tests/test_operator_accessibility.py`

- [ ] Add rendered REDs for a lazy Installed Agents pane with stable rows grouped by provider and surface.
- [ ] Show only product-owned label, presence, support level, monitoring state, capacity state, and one explicit Configure or Refresh action.
- [ ] Never render raw paths, executable locations, bundle paths, account labels, prompts, model names, or provider errors.
- [ ] Preserve focus, selection, unsaved controls, window identity, pane identity, keyboard traversal, VoiceOver labels, and 200 percent text scale under generation-fenced updates.
- [ ] Installed-only surfaces remain absent from menu, mailbox, notifications, completion, and hardware presentation.
- [ ] Run focused rendered Settings tests and neighboring History, Accessibility, capacity, and provider UI tests.
- [ ] Record source receipts. Do not commit because authority forbids it.

## Task 7: Independent review and source acceptance

**Files:**

- Create: `.superpowers/sdd/2026-08-13-sidepulse-installed-agent-registry/final-report.md`

- [ ] Run the full provider, collector, hook, install, private-I/O, IPC, installed-registry, runtime, Settings, accessibility, notification, capacity-separation, and canonical invariant suites.
- [ ] Run full Ruff, Python compile, and diff checks.
- [ ] Mutation-check OpenCode removal, provider-label fallback, shell-string spawning, raw-copy forwarding, arbitrary-plugin detection, installed-to-lifecycle leakage, duplicate surface keys, stale inventory application, and Google quota-to-completion conflation.
- [ ] Request one independent correctness/lifecycle review and one independent security/privacy/install review.
- [ ] Remediate validated findings through fresh RED/GREEN cycles and re-review.
- [ ] Record that source acceptance does not prove a live installed OpenCode plugin, Google runtime, permission state, package contents, network behavior, or physical hardware behavior.
- [ ] Record source receipts. Do not commit because authority forbids it.
