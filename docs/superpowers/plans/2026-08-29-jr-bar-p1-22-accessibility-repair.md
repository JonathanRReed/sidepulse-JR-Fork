# JR Bar P1.22 Accessibility Repair Plan

Status: closed on 2026-08-29.

## Objective

Complete roadmap item 22 without changing the effect or settings semantics:

- make lid presets, signal patterns, and mode-animation previews reachable and activatable from the keyboard;
- give editors and preview controls explicit accessible names, descriptions, roles, and selection state;
- provide persistent on-screen guidance where the only explanation is currently a tooltip;
- make Reduce Motion suppress pane transitions and animated preview programs while preserving the same choices and state;
- prove the behavior in AppKit-backed tests and an actual rendered settings window.

## Source Inventory

- `src/sidepulse/native_ui.py` owns shared control construction and accessibility metadata.
- `src/sidepulse/settings_window.py` owns the visual preview choices and LED program editors.
- `src/sidepulse/status_bar_legacy.py` owns selection actions, selection-state refresh, and settings-pane transitions.
- `tests/test_settings_accessibility.py` will own focused P1.22 regression coverage.

## Implementation Order

1. Add failing focused tests for keyboard focus, activation, accessible metadata, visible help, and Reduce Motion behavior.
2. Add shared, non-destructive accessibility helpers to the native UI factory layer.
3. Wrap live LED thumbnails in native choice controls while retaining the live `VirtualLedView` and existing pointer actions.
4. Teach selection handlers to accept both native button senders and the legacy gesture sender, then keep visual rings and accessibility state synchronized.
5. Label the Studio and lid program editors and add persistent guidance close to the controls.
6. Render representative static thumbnails and skip pane crossfades when Reduce Motion is active.
7. Run the focused tests, fast gate, lint, compile/type checks, full test suite, and a rendered AppKit inspection.

## Evidence Boundary

Source and AppKit-window verification can prove control hierarchy, keyboard traversal, action dispatch, accessible metadata, selection state, and Reduce Motion policy. It cannot prove VoiceOver speech quality on every macOS release or physical Screen Bar behavior. Those remain part of installed-app and hardware QA in P1.23.

## Closure receipt

- Shared control factories now attach accessibility labels and help to the
  real AppKit control, not just the visible row label.
- Lid presets, signal patterns, and state-animation thumbnails are wrapped in
  native keyboard-focusable radio-style controls while keeping the existing
  live `VirtualLedView` previews and pointer activation.
- Selection handlers accept both the new native choice buttons and the legacy
  gesture sender, and visual rings stay synchronized with accessible selected
  state.
- Studio, closed-lid, and open-lid program editors expose explicit
  accessibility labels and help, and the panes now include visible keyboard
  guidance near the editor and lid preview controls.
- Reduce Motion suppresses settings-pane crossfades and swaps animated lid,
  signal, and state preview programs for static representative colors without
  changing the selected choice.
- Preview and Reduce Motion policy now lives in the focused
  `settings_preview_policy.py` boundary. Direct single-color preview programs
  paint synchronously without starting the WASM animation renderer, so the
  static accessibility substitute is immediate and deterministic.
- A focused AppKit-backed accessibility suite passed 6 tests, `make fast`
  passed with 84 contract tests, 139 fixture and schema tests, and 297 focused
  tests, and the complete suite passed 6,618 tests plus 7 subtests with the
  four known Python 3.12 multiprocessing fork warnings.
- The verified source fingerprint stayed
  `b7abbb9a1b189de1d1b2cd048c458111b2a661dd36f08fe38e5f2669a4f49859`
  across the final complete-suite run, and independent review returned no
  findings.
- Separate isolated source AppKit processes rendered the complete Animations,
  LED Behavior, and Color Studio state-animation surfaces. That inspection
  exposed and fixed a zero-width Studio editor document view and a compressed
  saved-look action row, then confirmed painted static previews, visible
  captions and guidance, focus rings, and labeled selected-state controls.
  No installed-app, VoiceOver-on-device, or physical-hardware proof was
  claimed.
