# JR Bar P2.24 Explicit Application Composition Root Plan

Status: completed on 2026-08-29.

## Objective

Complete roadmap item 24 without changing visible behavior:

- assemble the AppKit status-bar application through one explicit, idempotent
  production composition root;
- make ordinary imports side-effect free with respect to controller, menu, Screen
  Bar, settings-navigation, and settings-window wiring;
- remove the legacy settings-window namespace injection;
- retain the public status-bar facades and all source, console-script, frozen-app,
  and LaunchAgent entrypoint contracts;
- prohibit new business behavior from being added to the retained legacy
  controller.

## Source Inventory

- `src/sidepulse/status_bar_legacy.py` owns the retained AppKit controller,
  lifecycle, delegate creation, and event loop.
- `src/sidepulse/_status_bar_production.py` owns the production controller layer.
- `src/sidepulse/provider_usage_status_bar.py` owns the provider-usage controller
  layer and the shipped foreground main.
- `src/sidepulse/status_bar.py` owns the compatibility facade and direct-module
  entrypoint.
- `src/sidepulse/settings_window.py` now declares its dependencies explicitly,
  with cycle-safe helpers in `src/sidepulse/settings_window_controls.py`.
- `src/sidepulse/settings_category_runtime.py` and
  `src/sidepulse/screen_bar_runtime.py` expose explicit, idempotent installers.
- `src/sidepulse/cli_entry.py`, `src/sidepulse/__main__.py`,
  `packaging/sidepulse_entry.py`, and `pyproject.toml` define the public startup
  routes that must continue to converge on the same foreground main.

## Compatibility Boundary

- Preserve `sidepulse.status_bar.StatusBarController is
  sidepulse.status_bar.JRStatusBarController` after explicit composition.
- Preserve the facade's read, write, delete, directory, source-introspection,
  and direct-module behavior.
- Preserve `sidepulse status-bar --foreground`, `python -m sidepulse`,
  `python -m sidepulse.status_bar`, the `agent-status-bar` console script, and the
  frozen Finder launch.
- Keep the packaged installer and LaunchAgent ownership rules unchanged.
- Keep lifecycle ownership in the active controller chain and close services in
  the existing production, provider-usage, then retained-runtime order.

## Implementation Order

1. Add red contract tests for import purity, ordered and idempotent composition,
   converged entrypoints, settings-window injection removal, and the retained
   controller surface ratchet.
2. Replace the 30 ambient settings-window dependencies with explicit imports or
   narrow cycle-safe lookups. Delete `_install()` and its retained-runtime call.
3. Add `application_composition.py` as the only production assembly boundary.
   It must install the production controller, provider-usage controller,
   settings navigation, Screen Bar runtime, and menu wrapper in a fixed order,
   and return a stable composition receipt when called repeatedly.
4. Convert `_status_bar_production.py` and `provider_usage_status_bar.py` from
   import-time mutators to explicit installer helpers. Keep their controller
   classes importable without changing retained runtime state.
5. Route every foreground main through the explicit composition root before
   AppKit delegate creation. Keep headless commands and package imports lean.
6. Add lifecycle reentrancy protection for launch and termination without
   moving subsystem ownership out of the active controller chain.
7. Add a semantic legacy-controller ratchet so its method surface can shrink but
   cannot grow without an intentional contract update.
8. Run focused contracts, Ruff, import-cost checks, the fast gate, the complete
   suite, and an isolated rendered AppKit startup/settings smoke.

## Parallel Ownership

- Settings lane: `settings_window.py` plus its injection-ratchet test only.
- Contract lane: new composition tests and entrypoint contract additions only.
- Core lane: composition root, controller installers, retained runtime seam, and
  public foreground entrypoints.
- Review lane: read-only architecture and regression review after integration.

Workers share the dirty worktree. Each lane must preserve other edits and must
not revert, stage, commit, push, package, install, or publish.

## Evidence Boundary

Unit and AppKit-backed source verification can prove import purity, deterministic
assembly, facade compatibility, controller selection, lifecycle idempotence, and
the rendered source startup path. It cannot prove the stale installed SidePulse
bundle, notarization, Gatekeeper, physical Dot hardware, or a current packaged
upgrade. Those remain independent candidate-bound gates.

## Rollback Boundary

The rollback unit is the new composition module plus its explicit installer and
entrypoint calls. It must not require reverting extracted runtime services,
settings schemas, hardware writers, packaging policy, or public facade names.

## Completion Receipt

- `src/sidepulse/application_composition.py` is now the single explicit,
  idempotent composition root for the foreground AppKit runtime.
- Importing `status_bar.py`, `_status_bar_production.py`, and
  `provider_usage_status_bar.py` no longer mutates the retained runtime,
  installs settings navigation, starts the Screen Bar runtime, or kicks off
  device-refresh work.
- `settings_window._install()` and the ambient `globals()` namespace injection
  were removed. The extracted `settings_window_controls.py` keeps the settings
  module below the existing architecture ceiling without adding new runtime
  behavior to the retained monolith.
- Every foreground path now converges on `status_bar_legacy.main()`, which
  composes once and then hands off AppKit delegate creation to
  `run_status_bar()`. Finder and packaged CLI startup remain lazy about the
  provider host import.
- Launch and termination are reentrancy-guarded across the retained,
  production, and provider layers.

## Verification Receipt

- Focused composition and contract tranche:
  `61 passed` via
  `./.venv/bin/python -m pytest -q tests/test_application_composition.py tests/test_integration_cli_entrypoint.py tests/test_architecture_ratchets.py tests/test_settings_window_injection_ratchet.py tests/test_status_bar_facade_contract.py tests/test_status_bar_adapter_reload_contract.py tests/test_status_bar_production_boundary.py tests/test_settings_import_order.py tests/test_provider_usage_status_bar_contract.py tests/test_settings_screen_bar_wiring.py tests/test_packaging_contract.py tests/test_hook_import_cost.py`
- AppKit-backed lifecycle and settings smoke:
  `25 passed` via
  `./.venv/bin/python -m pytest -q tests/test_status_bar_lifecycle_contract.py tests/test_settings_accessibility.py tests/test_capacity_panel_wiring.py`
- Canonical fast gate:
  `JR Bar fast gate passed in 18.54s.`
- Complete suite:
  `6633 passed, 4 warnings, 7 subtests passed in 366.89s (0:06:06)`
