# Master plan — JR-BAR

Agreed 2026-08-13. Four pushes, sequenced; the rename ships as a
display name first and an identity migration much later.

Everything here was verified by reading current code or measuring the
live machine. Items marked SHIPPED landed in commit 724f2f9.

---

## Phase 0 — Correctness that was silently broken (SHIPPED)

1. **The app was deaf.** Every live hook event since the 0.2.1 install
   died in a `TypeError`; 8.5 MB of identical tracebacks; `latest.json`
   frozen for an hour. Installed hooks spoke the pre-hint raw-event wire
   format, the app expects `ProviderRefreshHint`. Hook runtime realigned
   on the machine; in code a legacy sender is now **refused and named**,
   and the dropdown offers the one click that fixes it.
2. **The crowd never rendered.** `_hardware_write_task` always took the
   single-color glance branch, so the projection branch — where all six
   blend modes live — was unreachable. Two agents lit one color no
   matter what mode was set.
3. **Presets stomped layout.** A preset now owns FEEL only. LAYOUT (the
   mode) and COLORS belong to the user, permanently.
4. **Plain-language names.** Everyone / Spotlight / Split / **Smooth** /
   One at a Time / Status Only; Breathe / Chase / Steady / Blink.

## Phase 1 — Performance and correctness (foundation)

Idle CPU measured at 18–27% on the running app; the menu lag is the
same root cause.

- **Menu open ran the mailbox projection 3–4×** (2× pure waste), fully
  synchronous on the click path. *Memoized per operator state — SHIPPED.*
- **73 settings handlers call the full 15 s `refresh_()`**, whose first
  step can be a 150–290 ms synchronous directory walk. Needs a
  lightweight settings-commit path that never runs ingestion.
- **`refresh_settings_window()` repaints ~33 controls plus a per-device
  loop** on every interaction regardless of the visible pane. Scope it.
- **Packaged bundle cannot import `sidepulse`** when launched the way
  launchd launches it — a live, reproducible test failure.
- **Process-boundary contract tests.** Both Phase 0 bugs were
  *reachability* failures invisible to 3,890 unit tests: a wire-format
  mismatch between two processes, and a branch that could never be
  taken. Test the seams, not just the units.

## Phase 2 — The Screen Bar on any display

The stated selling point, and the weakest architecture: everything is
derived from a measured MacBook notch, so a non-notch display gets a
degraded notch layout instead of a design of its own.

- An explicit **display geometry model**: notch / no-notch / external /
  future device, chosen per screen rather than inferred.
- **User-adjustable** position, width and height, with a live preview,
  for displays we cannot measure.
- A real **non-notch layout** — a floating bar the user can place —
  rather than a notch layout with the notch removed.
- **Alcove**: the bracket is inset ~4–6 pt from the true rounded corners
  (raw measured width with zero margin); the window level is a hardcoded
  near-`INT32_MAX` guess rather than a measured layer; compact mode never
  follows the capsule at all; and a dead parallel `AlcoveCapsuleTracker`
  implementation should be deleted.
- Future devices: the geometry model is the seam that lets a non-Mac
  display or a second hardware device plug in later.

## Phase 3 — Per-provider usage limits (CodexBar-grade)

- **A large, tested capacity plane already exists and is entirely
  unreachable**: `capacity_view.py` (1,139 lines) has zero importers;
  `provider_runtime.py` is instantiated only in tests; so are
  `capacity_authority.py` and `capacity_sources.py`. The domain design is
  done. The gap is adapters and UI wiring.
- **Only Codex has a working adapter.** Claude has three independent kill
  switches: `fetch_windows()` always raises, `with_claude_plan_limits_enabled()`
  ignores its argument and always writes `False`, and Claude is never
  registered for the `remote_quota_windows` capability. Every other
  provider returns `SOURCE_UNAVAILABLE` unconditionally.
- **The top menu item opens per-provider limits**, one row group per
  provider, every window (not just the first), with reset countdowns —
  and the same model rendered in Settings, so the two can never disagree.
- **Honest states** where no limit API exists: the policy layer already
  knows *why* (unsupported vs. link-only vs. permission-required), which
  is more honest than inferring it from a failed fetch.
- **Graphs**: non-wrapping fixed-width labels truncate mid-word, the same
  summary renders twice in one pane, and a year of sparse data draws as
  an unreadable spike. Redesign per CodexBar/T3.

## Phase 4 — Animations and the editor

- **New effects** from the verified DSL palette (what the firmware
  grammar actually expresses, validated against `sdled.wasm`).
- Fix the motions that are lies: Heartbeat, Knock and Blink currently
  render as the same shape at different ratios.
- **An animation editor**: live preview on the Screen Bar, real firmware
  validation as you type, a personal library, and burn-to-`INIT.LED` so
  your own animation boots on the hardware.

## Phase 5 — JR-BAR

Display name only, first: the app presents as **JR-BAR**, with SidePulse
as the hardware section inside it. The bundle identifier, LaunchAgent
label and state paths stay exactly as they are.

The identity migration is deliberately deferred: the bundle ID carries
Full Disk Access, Screen Recording and Notification grants, and the hook
commands written into every provider's config point at real paths.
Changing those without a migration is precisely the failure mode Phase 0
just dug the project out of.

---

## Standing rules adopted from this round

1. **Reachable or it isn't done.** Unit-tested but unreachable code is
   how a 1,139-line presentation module and every blend mode ended up
   dead in production.
2. **Test the seams.** Process boundaries and branch precedence, not
   just pure functions.
3. **Never fail silently.** A dropped event, a refused hook, a stale
   provider: say so where the user is already looking.
