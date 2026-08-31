# Local Verification

P3.39 has an isolated source-AppKit Clear Agents receipt at
`.superpowers/sdd/2026-08-30-jr-bar-p3-39-safe-clear-agents/task-5-renders/`.
Run it with:

```bash
.venv/bin/python \
  .superpowers/sdd/2026-08-30-jr-bar-p3-39-safe-clear-agents/render_clear_agents_receipts.py
.venv/bin/python -m pytest -q tests/test_clear_agents_native_receipts.py \
  tests/test_clear_agents_popover.py tests/test_clear_agents.py
```

The harness renders the production `ClearAgentsPopoverPresenter`, not a
recreated card. Its 16 PNGs cover preview, protected live work, stale refresh,
saving, failure, successful receipt, expired Undo, and undone states in native
Aqua and Dark Aqua. The adjacent `manifest.json` binds every image to its
SHA-256, the relevant source SHA-256 values, visible copy, button state,
keyboard loop, first responder, and native accessibility readback.
The current paired reruns matched exactly across 16 PNGs for preview,
protected live work, stale refresh, saving, failure, successful receipt,
expired Undo, and undone states in Aqua and Dark Aqua. The receipt suite passed
51 tests in 9.15 seconds. The manifest SHA-256 is
`cd2426c39405c53d6d8fb7191ac4fbdcb1a2201abe692bd4f3239550f46febe6`, the
aggregate image-set SHA-256 is
`9093ff93c7cf322cec77f9403b850a6d91b4b259cad837e2a785251b6ccd1a53`, and
the sorted per-PNG hash-list SHA-256 is
`469baee6e5ed56628575d0872e4acd2dd03dbcb709e1e38cf85a0591e8e00612`. The
manifest also pins production `clear_agents.py` at
`2eb339a3dc0f6998641740dc5c7739ad33633f9b66c1fa49045726272e5b3c21`,
production `clear_agents_popover.py` at
`477dcb0c286669ac9b5bdd0c35889f5785bee3faf391da30616ecebe33d9726d`,
production `clear_agents_store.py` at
`002b04b31dba6e455fb009ad1f851efabc661213158b86436c7ab289c1616927`,
production `status_bar_legacy.py` at
`278d4235a0ab443b97df5dec97e2b2ce77b8364939159898a1221d8fd5ba8a7a`, and
the receipt harness at
`4b7904f338bb6e0eae651f0942b852ccb4832ee568efc5e9e7bbe18dd206e2ba`.

P3.38 has a deterministic source-AppKit DND receipt at
`.superpowers/sdd/2026-08-30-jr-bar-p3-38-manual-scheduled-dnd/task-5-renders/`.
Run it with:

```bash
.venv/bin/python \
  .superpowers/sdd/2026-08-30-jr-bar-p3-38-manual-scheduled-dnd/render_dnd_receipts.py
.venv/bin/python -m pytest -q tests/test_dnd_native_receipts.py \
  tests/test_dnd_settings_pane.py tests/test_menu_projection.py \
  tests/test_compact_menu_wiring.py
```

The two complete harness runs matched exactly across 16 PNGs for off, manual
Mute, scheduled Dim, public-Focus Pause, manual Asks Only, scheduled Fully
Dark, temporary Resume, and Focus unavailable in Aqua and Dark Aqua. The
receipt suite passed 40 tests in 3.49 seconds. The manifest SHA-256 is
`1e364f70181495b01d33746b7d53423b00a2093601ce2b2284bcc5f7842bed26`,
the aggregate image-set SHA-256 is
`38d5e61971a27ed950fc5a450b64f33db3b5f93c57c59d2a48dc4d796992b6b5`,
and the sorted per-PNG hash-list SHA-256 is
`3a447a044181e2f47ab6f906f861de726aebba1fd7d5d9a7f89a68ac53aade9a`.
The manifest also pins the production DND pane at
`13e306df11fdfab540f1e498c4be3894ceba7b8208ae240d911fac8cf6fe8366`,
menu projection at
`2ebdfc23f9c6cd839f42670e27336622892fd7ba9bd0addc8317fc28450346dc`,
status-bar menu adapter at
`2fa701e4e3b20246b9f2a8fe4fc403c77b618c9d86eeeec0eea0aa090e7301db`,
and receipt harness at
`ecf51dbf0e51b7262ee079e7e79623b891ba654339b44f3acb51d067405ed982`.

The pure and retained-runtime tests pin five different modes. Mute keeps
visual truth while refusing banners, sounds, and notification webhooks. Dim
keeps visual and outbound admission while scaling brightness. Pause admits
critical asks, failures, escalation, and low battery. Asks Only admits only a
current actionable ask and its escalation. Fully Dark withholds every light
surface and outbound notification at an authoritative zero brightness that no
minimum-glow or escalation floor may raise. Interruption grants retain separate
banner, audible, and webhook fields.

The daily local-time schedule supports same-day and overnight intervals. Its
shared resolver advances a spring-forward gap to the first valid local second
and chooses the earliest valid fall-back fold epoch at or after the lower
bound. Temporary Resume suppresses only the local schedule. It does not clear
an active macOS Focus contribution. Public `INFocusStatusCenter` authorization
and coarse activity are authoritative; private named-Focus detail can tighten
an authorized active public Focus but cannot activate DND by itself. A fresh
macOS 27.0 observe-only source probe returned activity `unavailable`,
authorization `not_determined`, and operation `observe_only`. It did not
request permission or mutate TCC.

The retained controller starts once after menu and status-item construction,
owns one transition timer and one observer set, refreshes on the documented
activation, sleep, wake, clock, and time-zone edges, and closes DND before the
other lifecycle owners and native surfaces. Every compact-menu and Settings
selector routes through that controller. Settings and the compact root menu
show the same active sources and exact return time. Why This Light and local
health reuse their existing fixed-shape, content-free rows for DND mode,
source, and return-time facts.

Restrictive modes consume finite cues instead of retaining them for later.
This includes completion, connection, reminder, calendar, quota, battery
preview, signal test, peek, and Screen Bar status cues, including a cue armed
by an asynchronous worker after the restrictive transition. Ending DND can
show unresolved standing ask or failure truth, but cannot replay an expired
visual, sound, banner, or webhook. The final combined runtime aggregate with
the asynchronous calendar regression passed 750 tests in 15.86 seconds.

The final card, menu, controller, lifecycle, and expanded effect-site rereviews
are clean. The combined P3.38 tranche passed 1,759 tests plus 7 subtests in
364.65 seconds. All-source Ruff passed. The post-fix `make fast` passed in 59.71
seconds with 113 contract tests, 150 fixture tests, and 542 focused tests. The
complete suite passed 7,754 tests plus 7 subtests in 422.41 seconds with four
known multiprocessing fork warnings. All 555 bound `src/` and `tests/` files
retained fingerprint
`ad795efd9a755e4293d2dbe344a8feb95ee21b1e6f00d7c5df3c2b10f0b77270`
before and after the broad gates. P3.38 is closed in source.

These are source and isolated source-AppKit receipts. They do not prove live
Focus authorization or a real Focus transition, every locale or DST case,
installed-app VoiceOver or keyboard traversal, physical JR Bar or Screen Bar
output, signing, packaging, notarization, publication, updater behavior, or
release readiness. No app was installed and no permission was requested.

P3.37 has an isolated source-AppKit global-action receipt at
`.superpowers/sdd/2026-08-30-jr-bar-p3-37-configurable-global-actions/task-5-renders/`.
Run it with:

```sh
.venv/bin/python \
  .superpowers/sdd/2026-08-30-jr-bar-p3-37-configurable-global-actions/render_global_action_receipts.py
```

The harness renders the production `GlobalActionSettingsPane`, not a recreated
card. Its 16 PNGs cover unset, recording, active, local conflict, registration
refusal, save failure, malformed persisted settings, and cleared states in
native Aqua and Dark Aqua. The deterministic manifest binds source and image
SHA-256 values, visible text, control state, focus, and accessibility readback.
The aggregate image-set SHA-256 is
`dad8ea282d5733a9fe06951db345cedb7a10268a8b68b1a1f0c4d64f69483abf`, the
manifest SHA-256 is
`19b63294992173d14f79f820247486970118a8ce83f5512dda0d4838792f49a1`, and the
production pane SHA-256 is
`1dcd3fbfee3a3344669c3a12879afd96284399e824fe117dd4b39a5373dd068f`.

The combined focused P3.37 gate passed 219 tests and the final independent
whole-tranche review found no concrete issue. A fresh macOS 27.0 process
registered and unregistered an uncommon all-modifier F18 Carbon hotkey and
removed its application handler without synthesizing input or changing
settings. After two stale test contracts were corrected, the relevant repair
slice passed 88 tests, `make fast` passed in 23.67 seconds with 113 contract,
150 fixture, and 540 focused tests, and the complete suite passed 7,509 tests
plus 7 subtests in 229.53 seconds with four known multiprocessing fork
deprecation warnings. All 550 bound source/test files retained fingerprint
`199d313bdcd05e5a02f8d7d26d21f4fcbe05af464f4ea959047eb9ac6dfd5fe0` before
and after the complete run.

These receipts prove pure shortcut rules, injected registry lifecycle,
controller routing, source-native recording, and one reversible source Carbon
registration. They do not prove every cross-application conflict, every
keyboard layout, installed-app focus, VoiceOver speech, signed or notarized
packaging, publication, updater behavior, or release readiness.

P3.36 has an isolated source-AppKit answer-control receipt at
`.superpowers/sdd/2026-08-30-jr-bar-p3-36-answer-in-place/task-4-renders/`.
Run it with:

```sh
.venv/bin/python \
  .superpowers/sdd/2026-08-30-jr-bar-p3-36-answer-in-place/render_answer_in_place_receipts.py
```

The harness renders the production `AnnouncerStackPanel` answer-control view,
not a recreated card. Its 16 PNGs cover eight answer states in native
`NSAppearanceNameAqua` and `NSAppearanceNameDarkAqua`, and the adjacent
manifest binds each image to its SHA-256, the relevant source SHA-256 values,
selected identity, generation, visible text, native AX role, label, value, and
help readback. The aggregate image-set SHA-256 is
`5d868719abaff1ac901fd2f4eabde86e99f1c4752cd266f44869f0faa27bd466`, and the
production view SHA-256 is
`7f615bfe9efb47e6ca867eb8baa22f3d20d6e1967ed0fc22d3f5db48dfc8c93c`.

The same source process exercises real native button targets, reply-field
editing, and AppKit key events for Approve, Deny, Reply, Send, Retry, Cancel,
Jump, Escape, and Tab. The expanded panel remains nonactivating, and the
explicit answer surface stays capability-gated and source-owned. This is a
source and isolated source-AppKit receipt only. It does not prove installed-app
focus, VoiceOver speech or traversal, live provider requests or writes, physical
LED behavior, signing, notarization, packaging, publication, or release
readiness.

P3.35 has an isolated source-AppKit announcer receipt at
`.superpowers/sdd/2026-08-30-jr-bar-p3-35-multi-alert-announcer-stack/task-4-renders/`.
Run it with:

```sh
.venv/bin/python \
  .superpowers/sdd/2026-08-30-jr-bar-p3-35-multi-alert-announcer-stack/render_announcer_stack_receipts.py
```

The harness renders the production `AnnouncerStackPanel` content view, not a
recreated card. Its 20 PNGs cover one collapsed ask, multiple collapsed asks,
expanded unseen, expanded seen, and expanded Reduce Motion states in native
`NSAppearanceNameAqua` and `NSAppearanceNameDarkAqua`, plus one focused receipt
for Previous, Next, Open, Mark Seen, and Close in each appearance. The adjacent
`manifest.json` binds every image to its SHA-256, the relevant source SHA-256
values, plan generation and selected identity, actionable and unseen counts,
position, dimensions, focus target, visible text, and native AX role, label,
value, and help readback.

The same source process exercises real native button targets and AppKit key
events for Previous, Next, Open, Mark Seen by Space and `d`, Escape, and Tab.
Collapsed presentation remains non-key and refuses first responder. Expanded
presentation becomes key only after the current collapsed pointer path emits
Expand. Tab reaches real native buttons even when macOS Full Keyboard Access is
off, and the focused button plus all control objects survive in-place selection.
The Reduce Motion receipt records the presenter's immediate transition mode.
Long 80-character, empty-content fallback, CJK, and RTL questions retain exact
native field readback, two-line wrapping, tail truncation, and in-bounds geometry.

The focused source slice passed 19 tests with 89 deselected, and the owned Ruff
slice passed without findings. All 20 final PNGs were inspected for clipping,
dark-surface contrast, count and source truth, stable anchoring, position, seen
wording, non-color priority, control geometry, and visible native focus. These
receipts do not prove installed-app focus, VoiceOver speech or traversal, live
provider requests, physical LED behavior, signing, notarization, packaging,
publication, or release readiness.

P3.34 source-AppKit receipts render the real Colors > Screen Bar Settings pane
for all seven typed Alcove confidence states in Aqua and Dark Aqua, and record
the projection copy, action visibility, dimensions, and accessibility fields
in the adjacent manifest. The recovery receipt exercises the shared frame
boundary with Reduce Motion off and on, recording animated versus immediate
frame application even when the final frame is identical. AppKit static text
may expose its visible sentence as AXValue on readback; the manifest retains
the typed accessibility value and records that platform readback separately.
These are isolated source receipts, not installed-app, real-permission,
live-Alcove, physical-display, signing, notarization, or release evidence.

The final P3.34 checkpoint passed the affected Doctor, Settings, architecture,
import-order, lifecycle, and Alcove slice with 262 tests. `make fast` passed in
17.04 seconds with 112 contract, 150 fixture, and 536 focused tests. The
warning-hardened complete suite passed 7,244 tests plus 7 subtests in 163.67
seconds with four known multiprocessing fork deprecations. Its 524-file
source-test fingerprint remained
`1ea4b4e55e82f3674a1bbf3cba62311df8d8fe2a66e97ef781920bcbab1cfaa8`.
Final independent rereview returned no findings.

Screen Bar energy and thermal work uses the separate fail-closed procedure in
[`SCREEN-BAR-PROFILING.md`](SCREEN-BAR-PROFILING.md). Source tests do not stand
in for its seven runtime and raw Instruments traces.

Deterministic Screen Bar tests cover command-scoped batch reuse, finite-effect
horizon clamping, latest-wins replacement during an in-flight batch, cadence
stall invalidation, and single-step fallback. These checks establish the
source contract only. The separate profiling matrix is still required for CPU,
wakeups, energy, memory, and thermal claims.

Usage Center responsiveness has a separate source contract. Tests prove that
provider callbacks execute off the caller thread, cross-Mac merge evidence is
refreshed before main-thread dispatch, equal snapshot values reuse an atomic
memory memo, and apply, summary, checkbox, and menu projection paths contain no
settings or Keychain loader. This does not replace installed-AppKit timing and
interaction checks, which remain a later evidence gate.

Persistence-writer tests cover FIFO execution on one non-caller thread, safe
latest-snapshot replacement, ordered usage-percent appends, retry after an
accepted disk failure, bounded overload, one reserved shutdown-tail slot,
failure isolation, consent-revocation fencing, and controller-level normal and
saturated shutdown drains. These are source and local-process receipts. They do
not prove behavior after force-kill, power loss outside the leaf fsync contract,
or termination of an installed signed app.

Priority-device-writer tests cover distinct semantic slots on one physical
device, urgent admission under queue saturation, protected result retention
during a stalled main thread, lower-priority downgrade refusal, deadline and
generation fences, snapshotted display selection, calibration open and close,
and direct-flourish idle fencing. These checks establish queue and controller
semantics in source. They do not prove physical USB-volume timing, firmware
durability, installed-AppKit interaction, or a signed app's sleep and wake
behavior.

Power-hold tests cover canonical ordinary and closed-lid `caffeinate` commands,
safe migration defaults, independent agent, display, battery, and closed-lid
settings, and bounded live child replacement. On 2026-08-29, an isolated
source AppKit process rendered Settings > Power with four separate cards and
the titled window `JR Bar Settings: Power`. Its default projection reported
agent hold on, display hold off, battery continuation on, and closed-lid
policy Never. After writing agent hold off and display hold on to the same
isolated settings store, a fresh source process restored those two switch
states without changing the other choices. Focused AppKit tests separately
assert the switch accessibility labels and retained action ownership. This
verifies source rendering and persistence projection. It does not prove
user-click dispatch in that process, signed installed-app behavior, actual
sleep and wake, closed-lid thermal safety, privileged-helper behavior, or
physical-device continuity.

Ordered hook-ingress tests cover the strict versioned wire envelope, same-user
private socket, one 32-event FIFO, explicit full, closed, and invalid outcomes,
failure continuation, canonical redaction and dedupe parity, content-free
rejection receipts, provider bridge ordering, synchronous app-owned monitor
reconciliation, and shutdown drain through a deliberately delayed refresh
handler. Reproduce the source-machine timing check with:

```sh
.venv/bin/python scripts/benchmark_hook_ingress.py --samples 100
```

On 2026-08-29, this source tree on macOS 27.0 arm64 with Python 3.13.15 measured
100 live-listener samples at 39.454 ms median and 41.812 ms p95, with 100
accepted, no refusals, no failures, and no fallbacks. The server-down path
measured 117.559 ms median and 180.333 ms p95, with all 100 events completing
through synchronous fallback. These are local source-process measurements.
They do not prove frozen-client startup, installed-AppKit latency, signed-app
shutdown, another Mac, or release readiness. The benchmark uses a temporary
private home and state directory and reports only aggregate counts and timing.

Raw hook bytes are not diagnostic output. Durable ingress failures live at
`${XDG_STATE_HOME:-~/.local/state}/sidepulse/agent-monitor/hook-ingress-rejections.jsonl`
and contain only version, time, sequence, provider, and reason. Provider logs
contain the existing minimized canonical records.
An acknowledgement lost after connection is an ambiguous submission, not an
unavailable listener. The client exits without fallback because the FIFO may
already have accepted the event. Tests delay the server acknowledgement past
the client timeout and prove the server processes one copy while fallback
processes none.

The P1.17 local-health checkpoint covers interval render duty and delivered FPS, dropped-batch
totals, current and peak queue depth, physical-write timing, oldest visible
source age, registered and live worker counts, shutdown timing, refresh timing,
counter reset and saturation, missing evidence, fixed copy, and content-free
production wiring. On 2026-08-29, an isolated Python 3.13.15 AppKit process
rendered `JR Bar Source Preview: Why Is It Doing That?` with the existing
decision explanation followed by all nine rows and the detailed timing section.
The full text fit without scrolling, remained selectable in the accessibility
tree, and showed `Unavailable` for unobserved values. The signed installed app
continued running separately. This proves source rendering and in-memory
projection only. It does not prove signed installed-app behavior, Instruments
energy or thermal measurements, physical LED latency, sleep and wake behavior,
package identity, installation, publication, or release readiness.

The P1.18 current-light checkpoint adds a fixed, content-free context section
for the selected semantic and P1-P7 priority, oldest visible source age,
bounded current finite-cue suppressions, Scene availability, global surface
role, Focus/DND observation-policy-decision, Reduce Motion substitution, and
source-labeled active-output timing. Focused context, projection, panel,
controller, local-health, notification, and architecture coverage passed 667
tests, followed by 3 notification-authorization lifecycle tests, on 2026-08-29.
A separate isolated AppKit preview rendered 1,631
characters in a 620 by 660 point panel; Page Down and Page Up moved and restored
the scroll position, Command-A and Command-C copied the complete text, and a
refresh to a 99-character-shorter body clamped the existing selection. The
text view exposed the accessibility label `Why this light explanation` and
help describing selection and copying. The preview showed explicit unavailable
source age, Scene, Focus/DND, and timing states, and was terminated afterward.
This is source UI evidence only. It does not prove behavior in the signed
installed app, physical-device output, Instruments performance, package
identity, installation, publication, or release readiness.

The definitive stable-tree source gate then passed 6,552 complete-suite tests
plus 7 subtests. Ruff, compileall, dependency policy, tracked-file secret
scanning over 571 files, release-version validation, six architecture ratchets,
and `git diff --check` also passed. The bound `src/` and `tests/` fingerprint was
`252d59cb956db3dd6226fd7c99af82fa8b315c25b6decb4470821610696bd98a`.
The four warnings were the known Python 3.12 multiprocessing fork deprecation
warnings. This direct gate did not run builds, packaging, installation, signing,
notarization, installed-app UI, physical hardware, deployment, or release work.

The P1.19 fast-gate checkpoint adds one ordinary-change command:

```sh
make fast
```

On 2026-08-29 it passed in 6.89 seconds on Python 3.12.13, and an
independent clean rerun passed in 8.87 seconds. A later P2.24 rerun on Python
3.13.15 passed in 18.54 seconds after the composition-root changes. Each ran,
in order: Ruff, real imports for the package, hook client, settings,
status-bar facade, production controller, usage status bar, and Why-light
context, 88 contract tests, tracked-file secret scanning over 571 files, 139
literal fixture and schema tests, 297 focused semantic tests, bytecode
compilation, dependency policy, release-version validation, and
`git diff --check`. A targeted contract slice then passed 14 tests covering the
fast gate and release-gate source receipt. This is source verification only. It
does not run the complete suite, bootstrap tools, build artifacts, clean-install,
hardware, installed-app UI, signing, notarization, Instruments, publication, or
release work.

The P1.20 packaging checkpoint adds executable, isolated tests for signed and
unsigned PKG command flow, exact artifact naming, validated version sourcing,
missing tools, certificate failures, deterministic checksums, exact wheel and
source-distribution staging, malformed release evidence, and the then-current ZIP
and appcast non-output contract. The complete focused packaging and release
slice passed 131 tests. `make fast` passed in 7.91 seconds with 69 contract,
139 fixture and schema, and 222 focused semantic tests. A fingerprint-stable
complete suite passed 6,586 tests plus 7 subtests; Ruff, compileall, dependency
policy, the 571-file tracked secret scan, version validation, Bash syntax, six
architecture ratchets, and `git diff --check` passed. Final independent
rereview returned no findings.

These tests use isolated executable doubles and did not build real developer
distributions, invoke Developer ID identities or notarytool, run Gatekeeper or
Installer, publish a release, or touch physical hardware. They prove source
behavior and fail-closed release wiring, not release readiness.

The P2.32 updater checkpoint adds source and executable-double coverage for the
pinned Sparkle dependency, embedded-framework runtime boundary, update menu,
nested signing order and verification, safe ZIP construction, pinned Keychain
public-key ownership, cryptographic appcast and archive verification, stable
and beta encoding, retained-feed history, candidate-bound metadata and
receipts, strict version monotonicity, and version-before-feed publication
ordering. `make fast` passed with 112 contract, 150 fixture, and 521 focused
tests. The warning-hardened complete suite passed 7,203 tests plus 7 subtests
with four known multiprocessing fork deprecations. Its 523-file source-test
fingerprint remained
`e407004016e04a2ab06ec611fe46e1e59fef58f30168fae316d9a4e029c8c791`.
A native AppKit source render exposed the expected update menu and stable/beta
selection state. Final independent rereview returned no findings. A production
candidate still requires Developer ID signing, app and PKG notarization and
stapling, an installed `N` to `N+1` update, settings preservation, downgrade
refusal, and separately authorized publication.

The P1.21 deterministic-timing checkpoint removes correctness-critical sleeps
from timing-sensitive tests and adds explicit idle, completion, and close
contracts for provider sync, Alcove observation, Screen Bar publication,
notification delivery, usage hooks, IPC, and related background work. Usage
graph, Usage Center, and status-bar boundaries use injected clocks, while
effect and cadence simulations retain explicit fixed seeds. The timing ratchet
rejects wall-clock sleeps, embedded JavaScript timeouts, `join()`,
`join(None)`, `join(timeout=None)`, and unseeded test random generators.

The focused timing slice passed 455 tests. A provider-wrapper import-order
regression was reproduced and fixed by running the two fake-clock wrapper
probes in isolated subprocesses; the affected composition sequence then passed
902 tests plus 7 subtests. `make fast` passed with 84 contract, 139 fixture and
schema, and 291 focused tests. A source-fingerprint-stable complete suite
passed 6,612 tests plus 7 subtests. Ruff, compileall, dependency policy, the
571-file tracked secret scan, version validation, six architecture ratchets,
and `git diff --check` passed, and independent final review returned no
findings. The four warnings were the known Python 3.12 multiprocessing fork
deprecations. This is source verification only. It does not prove installed
app behavior, real notification-center completion, animation quality, physical
hardware timing, signing, notarization, packaging, publication, or release
readiness.

The P1.22 accessibility-repair checkpoint adds keyboard-focusable native
preview choices for lid presets, signal patterns, and state animations; shared
control accessibility metadata; explicit editor labels and help; persistent
keyboard guidance in the pane; and Reduce Motion parity for pane transitions
and preview programs. The focused AppKit-backed accessibility suite passed 6
tests. `make fast` then passed with 84 contract, 139 fixture and schema, and
297 focused tests. The complete suite passed 6,618 tests plus 7 subtests, with
the same four known Python 3.12 multiprocessing fork deprecation warnings. A
separate isolated AppKit process rendered `JR Bar Settings: Animations`,
selected the Animations pane, and exposed lid preview choices as
`AXRadioButton` controls with labels and selected state. Additional isolated
renders covered LED Behavior and the Color Studio state-animation surface;
they confirmed visible captions and guidance, painted static previews, focus
rings, and usable editor and saved-look layouts. Direct single-color Reduce
Motion previews paint synchronously without starting the WASM renderer. The
source fingerprint remained
`b7abbb9a1b189de1d1b2cd048c458111b2a661dd36f08fe38e5f2669a4f49859`
through the final complete-suite run, and independent review returned no
findings. This proves source and source-AppKit control behavior only. It does
not prove installed-app VoiceOver speech quality, Notification Center
behavior, physical Screen Bar output, hardware timing, signing, notarization,
packaging, publication, or release readiness.

The original P1.23 installed-app and hardware checkpoint intentionally closed
with failures and named blockers, not a passing candidate. The then-installed
SidePulse 0.5.0 bundle was signed on August 27 and predated the current source.
Its deep signature was valid, but Gatekeeper rejected it as an unnotarized
Developer ID app; it had no stapled ticket or package receipt. The process had
a 9,352 MB physical footprint, mostly Mach messages, and emitted 3,062
CoreDisplay port lookup failures during a 30-minute window. The current source
has replaced that CoreDisplay path: a 1,500-read DisplayServices probe stayed
at 27 to 28 MB with no Mach-message category or stderr output, and 191 focused
brightness and lifecycle tests plus 3 subtests passed.

The P2.25 brightness checkpoint extracts ambient and signal brightness policy
into `brightness_policy.py` while retaining display-brightness reads, Focus
observation, idle timing, night-hour checks, refresh triggers, and hardware
writes in the controller. The required red first failed with
`ModuleNotFoundError: No module named 'sidepulse.brightness_policy'`. The pure
brightness contract then passed 13 tests. A focused policy, controller,
global-brightness, display-brightness, architecture, Focus, Screen Bar, and
device-brightness gate passed 82 tests in 2.56 seconds. `make fast` passed in
13.69 seconds with 95 contract tests, 139 fixture and schema tests, and 298
focused tests. An independent bounded static review found no defect. The
frozen-tree complete suite then passed 6,891 tests plus 7 subtests in 321.58
seconds with the four known Python multiprocessing fork warnings. The bound
`src/` and `tests/` fingerprint covered 494 source/test files and remained
`418660d98ec4743110bf8665c035dd82cb3b7952a8212d30b5aee1a81bbefe1f`
before and after the complete run. This is source verification only. It does
not prove installed-app brightness behavior, physical LED output, Screen Bar
visual quality, packaging, signing, notarization, publication, or release
readiness.

The final P2.25 batch moved completion visibility and acknowledgement planning
into `completion_visibility.py`, and bounded Screen Bar wording into
`announcer_content.py`. Pure and architecture coverage passed 31 tests in 3.13
seconds; focused completion, mailbox, menu, freshness, motion, lifecycle, and
composition integration passed 136 tests in 4.20 seconds. Independent review
found one `projection=None` Screen Bar crash, which received a direct
controller regression test; the post-fix gate passed 32 tests in 2.91 seconds,
and rereview reported no findings. `make fast` passed in 17.95 seconds with 96
contract tests, 139 fixture and schema tests, and 298 focused tests. The
complete suite passed 6,905 tests plus 7 subtests in 294.05 seconds with the
four known Python multiprocessing fork warnings. All 498 bound `src/` and
`tests/` files retained fingerprint
`2fdb3ac85451c835c6451c2934f782434c82d810a28505aa2df2dedae653dc05`
before and after the complete run. This is source verification only. It does
not prove installed-app acknowledgement behavior, Screen Bar visual quality,
physical LED output, packaging, signing, notarization, publication, or release
readiness.

One physical SidePulse Pro was directly discovered and classified. The live
app updated its LED program during inspection, and the observed eight-LED
program passed the firmware parser. No Dot was connected. The hardware smoke
script refused to write without `--confirm-write`, and no install, upgrade,
permission change, sleep/lid transition, eject, device removal, network
interruption, or system accessibility change was performed. The full case
matrix and exact blockers are recorded in
`docs/superpowers/plans/2026-08-29-jr-bar-p1-23-installed-app-hardware-qa.md`.
These receipts do not establish release readiness.

The follow-up 0.6.0 local candidate improved that boundary. Its app tree was
Developer ID signed, accepted by Apple's notary service, stapled, and accepted
by Gatekeeper. The user-local installed app matched that app tree exactly and
ran through the existing LaunchAgent. A bounded physical smoke wrote one
connected SidePulse device and restored `LEDS.LED` byte-for-byte. The outer
PKG remains unsigned and has no install receipt because no Developer ID
Installer identity is available. Installed UI and accessibility, Screen Bar
Instruments, Dot hardware, two-candidate updater, and publication checks remain
open. This candidate also predates the later repair that makes the arm64
bundle explicitly require macOS 11.0, so it is local evidence rather than a
final clean-tree release candidate.

GitHub Actions are manual-only. A clean macOS checkout is the authoritative gate because the application depends on AppKit, PyObjC, signing identities, macOS permissions, and optional physical SidePulse hardware.

## Development setup

```sh
./scripts/bootstrap-dev.sh
```

The script selects Python 3.10 or newer, creates `.venv`, and installs the fork with pinned development tools. It does not modify the system Python environment.

## Complete macOS verification

```sh
./scripts/verify.sh --fix
```

The gate runs:

1. Ruff over source, tests, packaging, and scripts.
2. Bytecode compilation and source/package/changelog version validation.
3. The complete pytest suite on macOS.
4. Wheel and source-distribution builds plus Twine metadata checks.
5. A clean-wheel installation in a temporary virtual environment.
6. Console-script, compatibility-module, resource, and repository-hygiene checks.

Every command preserves its exit status. No test output is piped through another command.

## Portable verification

A non-macOS machine cannot certify AppKit, PyObjC, TCC, signing, or hardware behavior. It can run the deterministic rescue gate:

```sh
./scripts/verify.sh --portable
```

## GitHub-hosted macOS runners are informational only

A hosted `macos-latest` runner has no logged-in window session, no
Screen Recording grant, no `bun`, a python.org framework interpreter
the installer-safety check rightly refuses, and shared-tenant timing.
A full-suite run there fails a known set of environment-coupled tests
(observed 2026-08-19: alcove honesty, installer transaction, screen-bar
motion, agent-browser p95, OpenCode-under-bun, Codex trust refresh)
while the same commit passes 100% on a real Mac. Treat hosted macOS
results as informational; the authoritative full gate is a clean local
checkout or the self-hosted workflow
(`.github/workflows/self-hosted-macos.yml`).

## Signed package verification

```sh
APP_SIGN_IDENTITY='Developer ID Application: …' \
INSTALLER_SIGN_IDENTITY='Developer ID Installer: …' \
NOTARY_PROFILE='sidepulse-notary' \
SIDEPULSE_VERIFY_MACOS_PACKAGE=1 \
./scripts/verify.sh --no-bootstrap
```

## Release

After the rescue branch is merged into `main`, use the owner-Mac release gate
from a dedicated release account. It requires signing identities, a
notarytool profile, measured performance JSON outside the checkout, physical
hardware confirmation, installed-upgrade authorization, and separate
uninstall authorization. A differently versioned signed JR Bar installation
and its package receipt must exist before the gate starts. The uninstall check preserves settings but removes
external JR Bar integrations before reinstalling the exact PKG.

```sh
export APP_SIGN_IDENTITY='Developer ID Application: …'
export INSTALLER_SIGN_IDENTITY='Developer ID Installer: …'
export NOTARY_PROFILE='sidepulse-notary'
export SIDEPULSE_PERFORMANCE_EVIDENCE='/absolute/path/performance-evidence.json'
export SIDEPULSE_HARDWARE_CONFIRM=1
export SIDEPULSE_RUN_INSTALLED_UPGRADE=1
export SIDEPULSE_RUN_UNINSTALL=1
./scripts/verify_macos_release.sh

# Publication remains a separate, explicit action.
./scripts/publish_release.sh
```

The release script refuses dirty or non-main trees, validates the exact current
version, builds the signed and notarized authoritative PKG, and records
candidate-bound verification receipts for source, performance, signatures,
Gatekeeper, stapling, package contents, entitlements, hardware, upgrade,
uninstall, clean install, checksums, SBOM, and manifest generation. Publication
remains a separate explicit action through `./scripts/publish_release.sh`. It
does not publish the upstream-owned `sidepulse` project name to PyPI.

The P2.24 explicit-application-composition-root checkpoint moved foreground
assembly into `application_composition.compose_status_bar_application()`,
removed `settings_window._install()` and ambient namespace injection, made
`status_bar.py`, `_status_bar_production.py`, and
`provider_usage_status_bar.py` import-pure with respect to controller and menu
rebinding plus runtime bootstrapping, and routed Finder plus direct-module
startup through the same explicit composition boundary. On 2026-08-29, the
focused composition tranche passed 61 tests, the AppKit-backed lifecycle and
settings smoke passed 25 tests, `make fast` passed in 18.54 seconds with 88
contract, 139 fixture and schema, and 297 focused tests, and the complete
suite passed 6,633 tests plus 7 subtests with four known Python 3.12
multiprocessing fork deprecation warnings. This proves source composition,
entrypoint convergence, lifecycle idempotence, and source-AppKit settings
behavior only. It does not prove an installed bundle, signing, notarization,
physical hardware, or release readiness.

The first P2.25 notification-arbitration extraction moved notification binding
pruning, content-free delivery planning, completion-notification eligibility,
and token-to-work-key resolution into `notification_arbitration.py`, while
keeping actual Notification Center delivery and session opening inside the
retained controller. On 2026-08-29, the focused notification tranche passed 27
tests with 901 deselections, `make fast` passed in 26.75 seconds with 88
contract, 139 fixture and schema, and 298 focused tests, and the complete
suite passed 6,639 tests plus 7 subtests with four known Python 3.12
multiprocessing fork deprecation warnings. This proves the extracted pure
notification boundary and repo-wide regression coverage in source form only. It
does not prove installed-app notification delivery, macOS Notification Center
presentation, signing, notarization, or hardware behavior.

The P2.25 signal-selection extraction moved the exact 18-claim per-device LED
precedence table and asks-only muting policy into `signal_selection.py`. The
retained controller still reads live clock, settings, battery, Focus, lifecycle,
weather, timer, and quota facts, and it still reports a failing claim once per
display kind. Pure tests pin every pairwise precedence relationship, lazy
short-circuiting, non-evaluation of muted claims, fallback, and exception
propagation. Controller and architecture tests pin fact-map completeness,
continuation to a later claim after an earlier failure, duplicate battery-kind
logging, and the exact completion-to-all-clear boundary. The focused extraction
gate passed 255 tests, and the post-review selector/controller slice passed 185
tests. A refresh-hint side effect was removed from the hook-ingress durable-byte
equivalence test; that test then passed in 40 fresh pytest processes. `make fast`
passed in 20.22 seconds with 91 contract, 139 fixture and schema, and 298
focused tests. The complete stable-source suite passed 6,821 tests plus 7
subtests in 284.49 seconds, with the four known Python multiprocessing fork
warnings. The before and after source/test manifest remained identical at
`51402bf88542a40fb673fd2a2e1cb6637924009ff6692558c40ceb127dad4eec`, and an
independent bounded review found no signal-selection defects. These are source
receipts only. They do not prove installed-app output, physical LEDs, Screen
Bar rendering, signing, notarization, packaging, publication, or release
readiness.

The P2.25 effect-selection extraction moved the four current global effect
catalogs and their payload validation into `effect_selection.py`. Blend modes,
feel presets, preview scenarios, and provider animations now have immutable
option descriptors, fail-closed pure selection plans, and one reverse-selection
helper shared by the Agents pane and Color Studio. AppKit target/action dispatch,
settings persistence, refresh, status copy, and preview delivery remain in the
existing adapters. Pure and popup tests also pin unknown-value no-ops and the
controller-level Custom preset behavior. The isolated source-AppKit effect,
Color Studio, defect, and accessibility run passed 123 tests; the post-review
focused effect/UI run passed 119 tests with 62 deselections. `make fast` passed
in 16.96 seconds with 93 contract, 139 fixture and schema, and 298 focused
tests. The complete stable-source suite passed 6,872 tests plus 7 subtests in
293.31 seconds with the four known Python multiprocessing fork warnings. All
492 source/test files were identical before and after at fingerprint
`f21ec6dcd7c55646afe1fdb2b7b379663db341a48526b21001d40c6f8a767040`, and
an independent static review found no effect-selection defects. These are
source and isolated source-AppKit receipts only. They do not prove the stale
installed app, physical LEDs, animation quality on a display, packaging,
signing, notarization, publication, or release readiness.

The P2.25 device-targeting audit proved that the old single-device selector,
preferred-target helpers, and Pro-before-Dot priority table had no runtime,
Objective-C selector, packaging, or test call site. JR Bar's live path already
targets each connected inventory candidate through its own per-device
controllers and bounded hardware-worker slot. The cleanup removed that dead
chooser, its scalar controllers, singular target method, and scalar display
state, then added syntax and controller behavior ratchets for the multi-device
path. A separate order-dependent test fixture was repaired by initializing the
lazy device-identity cache before patching its snapshot; its four race tests now
pass from a fresh process. Focused architecture, identity, inventory,
projection, lifecycle, and composition coverage passed 42 tests, while the
controller device, keepalive, and hardware slice passed 88 tests with 762
deselections. `make fast` passed in 14.11 seconds with 94 contract, 139 fixture
and schema, and 298 focused tests. The complete stable-source suite passed
6,874 tests plus 7 subtests in 286.40 seconds with the four known Python
multiprocessing fork warnings. All 492 source/test files were identical before
and after at fingerprint
`2683196ff1e0c08f743fae839bafe6aedbdb06207d558d75703ec0103761a2af`, and an
independent static review found no device-targeting defects. These are source
receipts only. They do not prove installed-app hot-plug behavior, device
removal, physical output, packaging, signing, notarization, publication, or
release readiness.

The P2.26 through P2.31 architecture tranche added typed collection,
presentation, and sync settings projections; explicit product-capability
declarations; composite provider-instance identity across settings, usage
state/store/sync, menus, Usage Center, consent, credentials, reconnect, and
refresh actions; dated synthetic fixture ownership; stale-write refusal for
usage, sync, and browser-consent documents; and revision-fenced refresh
publication receipts. An independent review found and drove repairs for exact
Settings checkbox identity, durable-versus-presentation state separation,
non-default Claude reconnect routing, and browser-consent stale writes.

The isolated source-AppKit Usage Center rendered separate `Claude · personal`
and `Claude · work` cards with 72% and 24% meters and separate Reconnect
actions. The first render exposed bottom-aligned scroll content; the flipped
document repair moved the heading and cards to the visual top. The status-bar
facade also crossed its 40,000-byte ratchet at 43,441 bytes, so exact action
routing moved to `provider_usage_controller_actions.py`; the reviewed result is
39,042 bytes.

Final `make fast` passed in 27.91 seconds with 97 contract tests, 150 fixture
tests, and 479 focused tests. The complete suite passed 6,999 tests plus seven
subtests in 176.18 seconds with the four known Python 3.12 multiprocessing fork
warnings. The 518-file `src/` plus `tests/` fingerprint was identical before
and after at
`8d9265c1ab88b4dd9fdd0ed36c04ba1bab800c617de0372fe198760dd3ea73d1`.
Caching the import-reachability ratchet reduced its focused three-file command
from over a minute of CPU work to 2.73 seconds without weakening its result.

These are source and isolated source-AppKit receipts. The subsequent P2.29
consumer tranche closes the per-instance profile continuation described at
this checkpoint. This checkpoint does not prove an installed bundle, live account switching,
physical LEDs, Screen Bar hardware, signing, notarization, packaging,
publication, updater behavior, or release readiness.

The P2.33 adaptive-refresh acceptance tranche extracted the existing cadence
ladder into `adaptive_refresh.py`, with typed bounded reasons for constrained,
menu-recency, idle, ambient-visibility, degraded-source, and reset-watch
behavior. `ProviderUsageService` exposes the current scheduled cadence without
I/O, and the real menu-open path now uses one bounded admission helper while
provider collection remains on existing workers. Independent review caught and
drove a repair for a cached idle deadline that did not move forward after a
menu visit. Menu attention now shortens that accepted deadline to 120 seconds,
and ambient visibility shortens it to 300 seconds, without collecting provider
data.

The final fast gate passed in 23.75 seconds with 97 contract, 150 fixture, and
497 focused tests. The complete suite passed 7,017 tests plus seven subtests in
154.44 seconds with the four known Python 3.12 multiprocessing fork warnings.
All 514 source/test files were identical before and after at fingerprint
`d9db33019c89fa112cea47639afa42c58d6cb9f4675224b166791d922fe2f7d7`, and
the follow-up independent review found no remaining defect in the repaired
surface. These are source receipts only. They do not prove installed idle CPU,
menu-open p95, or live menu-tracking I/O. Those claims still require the
existing current-candidate 300-second Instruments evidence document.

The P2.29 profile-consumer tranche made the five non-secret exact-instance
choices durable and connected them to retained consumers. Exact labels and
color overrides now drive the Usage Center, compact usage menu, Quota Runway,
and Settings cards. Exact retention governs native percentage history without
changing Operator History. Outbound remote sharing fails closed, with `never`
excluding an instance and `status_only` removing token, cost, model-count,
account-label, and machine-usage observations. Non-default session actions
resolve through the exact `WorkKey.source_key`; the default instance preserves
the legacy provider/origin router.

Independent review found and drove two integration repairs. Cached Settings
cards now refresh their headings, accessibility labels, text values, popup
selections, and committed represented payloads after a save, and restore from
the current committed snapshot after a failed save. The merged cross-Mac memo
now invalidates when its sharing signature changes, expires after a 30-second
monotonic lease even for identical local snapshots, and rejects late worker
publication across a policy-generation fence. The status facade closes at
38,132 bytes under its 40,000-byte ratchet.

The final focused architecture and P2.29 run passed 174 tests. Ruff and diff
hygiene passed. `make fast` passed in 16.05 seconds with 102 contract tests,
150 fixture tests, and 517 focused tests. The complete suite passed 7,077 tests
plus seven subtests in 175.77 seconds with the four known Python 3.12
multiprocessing fork warnings. All 516 source/test files were identical before
and after at fingerprint
`804c9abed497abb90d31dfad28510cd1f04ddfa8c4fc8b630218741424046b99`.
These are source and isolated source-AppKit receipts only. Installed account
switching, physical output, packaging, trust, publication, and release remain
separate evidence gates.
