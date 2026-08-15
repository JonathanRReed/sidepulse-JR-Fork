# SidePulse deep production-readiness audit

Date: 2026-08-15  
Audited commit: `4e09378b5a5ef03fc81617d5625a8da7d789d77d` (`main`)  
Scope: runtime correctness, macOS threading, performance, device safety, persistence, security, privacy, tests, packaging, supply chain, release engineering, repository governance, and product completeness  
Verdict: **not production-ready yet**

## Executive verdict

SidePulse has several unusually strong foundations. Its private-file transactions, device-write hardening, local cloud-ingest boundary, remote-peer reader, credential handling, canonical provider facts, capacity authority, navigation allowlists, and packaged-bundle verification are better than those in many mature desktop utilities.

The application is still not ready to be called a production release. The main blockers are concentrated at the boundaries between those strong pure modules and the historical AppKit controller:

1. a battery `ioreg` subprocess still runs synchronously from the main refresh path and has no timeout;
2. the product-wide 2 Hz flash-safety rule is bypassed by the escalation takeover and by preview/test paths, which can reach 10 Hz;
3. a background hardware worker reads live AppKit window state;
4. per-device `resting_glow` is loaded and used but omitted during serialization, causing silent settings loss;
5. settings write a schema version but never negotiate it during load, so rollback or downgrade can destroy future fields;
6. final LED programs are size-checked but are not passed through the real firmware parser at the universal device-write boundary;
7. the merged code has not completed the clean-macOS, AppKit, physical-hardware, signing, notarization, and installed-upgrade gate required by its own documentation.

The visible lag is therefore credible and explainable. It is not only an animation problem. The historical controller still treats `refresh_()` as a universal reconciliation operation, has more than one hundred internal calls to it, performs synchronous work from UI-triggered paths, repaints broad settings state, and mixes AppKit ownership with worker execution.

### Readiness score

| Area | Score | Assessment |
| --- | ---: | --- |
| Domain modeling and pure policy | 8/10 | Strong typed facts, reducers, bounded models, and authority gates |
| Filesystem and device-write security | 9/10 | Excellent no-follow, identity, atomicity, and readback discipline |
| Runtime correctness | 5/10 | Several confirmed boundary bugs and dormant paths |
| macOS threading and lifecycle | 4/10 | Main-thread blocking plus off-main AppKit access |
| Performance architecture | 4/10 | Thoughtful render policy, but universal refresh remains dominant |
| Test quality | 6/10 | Large suite, weak seam coverage, one 1 MB test monolith |
| Supply chain and release engineering | 4/10 | Good bundle verification, weak reproducibility and governance |
| Security and privacy | 7/10 | Strong local boundaries, weaker webhook, entitlement, and release policy |
| Product completeness | 4/10 | Core agent monitoring exists; approved T3, CodexBar, native host, and Studio work is pending |
| Maintainability | 3/10 | 752 KB controller, dynamic namespace injection, broad lint exemptions |

**Overall: 54/100.** This is a capable internal alpha with strong subsystems. It is not yet a production-grade application.

## Audit method and limitations

This was a source-first review of the complete recursive `main` tree, high-risk runtime modules, build and release scripts, tests, documentation, and merged production specification. Findings were cross-checked across producers, consumers, persistence, and UI call sites rather than inferred from module names.

The available environment could not obtain a local macOS checkout and did not expose the dedicated Codex Security deep-scan server. The complete test suite, Instruments, Thread Sanitizer, Address Sanitizer, signed package, notarization, installed upgrade, and physical SidePulse hardware were therefore **not executed during this audit**. Any claim requiring runtime evidence is labeled as a required validation rather than a completed result.

Static findings marked **confirmed** are directly supported by reachable code. Findings marked **process gap** are release conditions that have not been demonstrated. Findings marked **architectural risk** require runtime measurement but have a concrete code path that justifies the risk.

## Release blockers

### SP-AUD-001: battery collection can indefinitely block the main thread

Severity: **Release blocker**  
Confidence: **Confirmed**

`StatusBarController.refresh_()` calls `self.read_battery_snapshot()` synchronously. The uncached path calls `battery.read_battery_snapshot()`, which runs `/usr/sbin/ioreg` through `subprocess.run` without a timeout.

This violates the documented rule that menu and status refreshes do not fork subprocesses on the main thread. If `ioreg` stalls, the menu bar, settings, Screen Bar control, notification actions, and application termination can stall with it. Even when it does not hang, process creation and plist parsing add avoidable latency to a broad refresh path.

Required fix:

- move battery sampling into `RuntimeWorkerDomain.OS_POLL` or a dedicated latest-wins worker;
- use a strict deadline and subprocess timeout;
- publish an immutable `BatterySnapshot` to the main thread;
- retain last-known-good data with a visible stale status;
- never call the uncached battery reader from `refresh_()`, menu tracking, settings handlers, or draw callbacks.

Required tests:

- fail if `subprocess.run` is invoked from the main thread during refresh;
- prove a stalled battery probe times out and leaves the last-known-good snapshot intact;
- prove multiple refresh requests coalesce into one probe;
- prove shutdown cancels or abandons the probe within a bounded deadline.

Acceptance criterion: no main-thread subprocess, filesystem walk, network request, fsync, or hardware write in a 30-minute Instruments trace covering normal use.

### SP-AUD-002: flash-safety enforcement is bypassable

Severity: **Release blocker**  
Confidence: **Confirmed**

The signal policy states that nothing may repeat above 2 Hz. `budgeted_style()` enforces a 0.5-second minimum cycle, but enforcement is call-site dependent rather than universal.

Confirmed bypasses:

- `escalation_takeover_program()` builds a repeating 0.45-second style and passes it directly to `style_to_program()`, producing approximately 2.22 cycles per second;
- settings thumbnails and live previews render the raw configured style;
- `test_signal_program()` renders the raw configured style;
- `SignalStyle` accepts a minimum speed of 0.1 seconds, so those paths can produce 10 Hz motion;
- `style_to_program()` performs no safety normalization of its own.

The distinction between a preview and a real signal is irrelevant to photosensitive-flash risk. A preview is still visible light, and the physical-device preview can still drive hardware.

The production vision also requires a stricter red-flash rule. That rule is not centralized in the current renderer.

Required fix:

Introduce one mandatory `SafePresentationCompiler` below every first-party and user-authored renderer. It must:

- enforce the general flash ceiling on Screen Bar, hardware, thumbnails, setup demo, Studio, tests, and escalation;
- apply a stricter saturated-red cadence rule;
- honor Reduce Motion and Differentiate Without Color;
- reject or deterministically transform unsafe programs;
- return structured diagnostics explaining every transformation;
- make direct device writes of uncompiled presentation text impossible outside a narrowly reviewed expert API.

Required tests:

- enumerate every signal kind, pattern, legal speed, surface, and preview path;
- property-test that compiled output never exceeds the safety envelope;
- assert escalation takeover, setup demo, settings previews, Studio previews, and test signals use the compiler;
- render the full critical-state language in grayscale and Reduce Motion modes;
- verify saturated red cannot enter the elevated risk band.

Acceptance criterion: one code-level enforcement point, with no first-party bypass and a reachability test proving all device and screen renderers call it.

### SP-AUD-003: per-device resting glow is silently deleted on save

Severity: **Release blocker**  
Confidence: **Confirmed**

`DeviceDisplaySetting` contains `resting_glow`. Loading parses it, runtime controllers use it, calibration profiles include it, and mutators update it. `DeviceDisplaySetting.to_dict()` omits the field.

Any later settings save serializes the device without `resting_glow`. Reload then falls back to the default, silently deleting the user’s setting. The existing test named as a resting-glow round trip does not create, save, and reload a real device entry, so it cannot catch this defect.

Required fix:

- serialize `resting_glow`;
- create a table-driven round-trip test over every persisted field in `DeviceDisplaySetting`;
- add a generic dataclass-to-schema coverage test that fails when a persisted field is not represented by the encoder and decoder;
- add an installed-upgrade fixture containing non-default values for every device setting.

Acceptance criterion: load, mutate an unrelated setting, save, reload, and byte-inspect without losing any device field.

### SP-AUD-004: AppKit is accessed from the hardware worker

Severity: **Release blocker**  
Confidence: **Confirmed**

Hardware writes correctly run in a `LatestWinsWorker`. However, `_sync_hardware_device()` calls `agent_render_colors()`, and that method queries `settings_window.isVisible()` and the active settings pane. This is live AppKit state being read from an arbitrary worker thread.

AppKit objects are main-thread-owned. A benign call can become a crash, deadlock, inconsistent state read, or hard-to-reproduce UI corruption after an operating-system update.

Required fix:

- compute an immutable `HardwareRenderContext` on the main thread;
- include effective colors, preview baseline, pane-preview policy, escalation stage, accessibility preferences, device overrides, and every other render input in the work request;
- forbid workers from reaching `NSWindow`, `NSView`, `NSMenu`, or controller-owned UI dictionaries;
- add debug main-thread assertions at every AppKit boundary;
- give worker-owned caches and controllers one explicit owner.

Required tests:

- inject AppKit stand-ins that raise when touched off-main;
- run hardware render preparation on a worker and prove it consumes only immutable values;
- use Thread Sanitizer on the native host and targeted stress tests on the PyObjC bridge.

Acceptance criterion: a static architecture test finds no AppKit imports or AppKit-object calls in worker executors.

### SP-AUD-005: settings schema version is written but not enforced

Severity: **Release blocker**  
Confidence: **Confirmed**

Settings emit `settings_schema_version`, but loading does not inspect or negotiate it. The decoder accepts known fields and discards unknown fields. An older build can therefore load a newer settings file and rewrite it without the fields it does not understand.

This makes rollback unsafe and turns a future schema upgrade into silent data loss. Keeping the version constant at `1` while the structure evolves also removes its value as a migration boundary.

Required fix:

- define an explicit settings envelope with current, minimum-readable, and minimum-writable schema versions;
- fail closed or enter read-only compatibility mode when a file is newer than the writer;
- preserve unknown fields when safe, or refuse to write;
- implement ordered, idempotent migrations with fixtures for every historical version;
- keep a verified backup until the migrated file has been read back successfully;
- expose migration status and rollback instructions in diagnostics.

Required tests:

- upgrade fixtures from every released schema;
- downgrade/newer-schema fixture proving no write occurs;
- unknown-field preservation test;
- interrupted migration and rollback test;
- concurrent writer identity test.

Acceptance criterion: no supported application version can silently remove a field written by a newer version.

### SP-AUD-006: final LED writes are not firmware-grammar validated

Severity: **Release blocker**  
Confidence: **Confirmed**

`device_writer.validate_led_text()` verifies only non-empty text, the 512-byte limit, and the 20-line limit. `AgentLedController` writes final generated programs through `write_led_program()` without universally invoking `SdLedWasmController.parse()`.

The animation editor understands the consequence: malformed text makes the firmware abandon the intended program and emit six saturated-red 150 ms flashes. Animation power-up burns are parser-gated, but ordinary runtime programs, previews, transformed programs, and future renderers do not share that universal gate.

Required fix:

- make the real firmware parser mandatory at the final post-transform device boundary;
- validate after brightness, resting glow, channel transfer, and every other textual transformation;
- on rejection, write a safe static-off program or retain the last known-good program, never send malformed text;
- expose a bounded product-owned reason code;
- cache validation by exact final program bytes;
- keep a narrowly scoped emergency bypass only for controlled development, never production.

Required tests:

- mutation-test generated programs and prove malformed variants never reach the writer;
- test every first-party state, blend, signal, device size, accessibility mode, and brightness setting against the packaged WASM parser;
- verify final transformed bytes, not pre-transform text;
- prove parser unavailability fails safely.

Acceptance criterion: no production hardware write can occur without successful firmware-grammar validation of the exact bytes being written.

### SP-AUD-007: the merged code has not passed its authoritative release gate

Severity: **Release blocker**  
Confidence: **Process gap**

The rescue work was merged with portable focused tests, bytecode compilation, and shell syntax checks. The repository itself states that full AppKit/PyObjC behavior, TCC continuity, physical hardware, signed packaging, notarization, and stapling require the owner’s Mac.

Those checks have not been demonstrated for the current `main` commit. The current app must therefore be treated as unreleased development code.

Required gate:

1. clean checkout of exact `main` commit;
2. `./scripts/verify.sh --fix` on supported macOS;
3. clean wheel installation and packaged-app import check;
4. real status-bar launch and quit/relaunch cycle;
5. settings migration from the last installed version;
6. Screen Bar exercise on notch, non-notch, external, sleep/wake, lock/unlock, Reduce Motion, and Low Power Mode;
7. SidePulse Dot and Pro write, unplug/replug, eject, and parse-failure tests;
8. Developer ID signing, TeamIdentifier verification, notarization, stapling, Gatekeeper assessment, and installed package launch;
9. install-over-old-version and rollback rehearsal;
10. Instruments capture against the production performance budgets.

Acceptance criterion: a signed release-evidence manifest committed or attached to the release, identifying hardware, OS, commit, artifact hashes, signing identity, tests, and measured budgets.

## High-priority findings

### SP-AUD-101: `refresh_()` remains a universal application reconciliation

Severity: **High**  
Confidence: **Confirmed architecture risk**

The historical controller still uses `refresh_()` for provider fallback ingestion, snapshot collection, canonical history, attention projection, battery state, connected-device observation, escalation, completion tracking, menu/status updates, hardware scheduling, settings projections, and diagnostics.

The controller contains more than one hundred references to `self.refresh_`. Settings actions frequently save and then request a full refresh. Several watcher result handlers also call the full path when only one domain changed.

This causes unrelated work to be coupled. A focus change can induce agent reconciliation. A weather edge can rebuild menu state. A settings toggle can touch provider ingestion. Every future feature increases the cost and regression surface of the same operation.

Required fix:

- implement the approved `CoreSnapshot` and `StateDelta` model;
- make each source publish scoped immutable facts;
- add domain-specific reducers and projections;
- keep full refresh only for cold start, explicit recovery, and diagnostics;
- coalesce normal updates for at most 50 ms while urgent transitions bypass the coalescer;
- apply stable row patches rather than rebuilding menu and settings structures.

Performance acceptance:

- menu-open P95 below 50 ms;
- settings-section switch P95 below 100 ms;
- no routine main-thread task above 16 ms;
- hidden Screen Bar idle median below 1% CPU;
- gentle motion below 3% CPU;
- no provider scan, process probe, hardware write, or persistence during menu tracking.

### SP-AUD-102: transcript fallback still performs synchronous filesystem work from refresh

Severity: **High**  
Confidence: **Confirmed architecture risk**

The collector added caches after a real `rglob` walked 2,445 transcript files on the main thread. The fallback is cheaper now, but `refresh_()` still calls transcript input-signature and ingestion logic synchronously. Every cache expiry or new-file discovery reintroduces directory and metadata work into a broad UI-sensitive path.

Required fix:

- move all transcript discovery and incremental tailing to a dedicated provider worker;
- use filesystem events where reliable, with a bounded periodic reconciliation fallback;
- publish normalized provider facts instead of handing monitor objects to the controller;
- expose last-success, scan duration, files visited, and stale state;
- enforce per-provider time and file budgets.

### SP-AUD-103: static analysis is deliberately blind to the two highest-risk files

Severity: **High**  
Confidence: **Confirmed**

`status_bar_legacy.py` is excluded from Ruff. `settings_window.py` suppresses undefined-name checks and injects the controller’s globals dynamically through `_install(dict(globals()))`. The public facade changes its module class and redirects `__file__` to the legacy implementation.

This prevents ordinary static tools from proving imports, names, ownership, and call boundaries. It also explains why a real undefined-variable defect reached `main` previously.

Required fix:

- stop adding behavior to the legacy controller;
- extract one bounded subsystem at a time with explicit constructor dependencies;
- replace global injection with typed view models and action protocols;
- enable Ruff and a static type checker on every extracted module;
- add an allowed-debt count that can only decrease;
- delete overridden legacy methods once their facade replacements are stable.

### SP-AUD-104: reachability testing checks imports, not execution

Severity: **High**  
Confidence: **Confirmed**

The repository’s own ratchet documents this limitation. A module passes if another module imports it, even when no production call reaches its behavior. `interruption_policy.plan_interruptions()` and substantial delivery-ledger planning are current examples: large, tested, imported, and not used by the live runtime.

This is the same class of defect that previously left blend modes, log janitors, and the capacity view unreachable.

Required fix:

- add call-reachability contracts for important entry points;
- create process-boundary and branch-precedence tests;
- instrument production feature activation and assert expected counters in smoke tests;
- delete dormant subsystems or adopt them as the single authority;
- prohibit parallel implementations of the same responsibility.

### SP-AUD-105: dependencies and release builds are not reproducible

Severity: **High**  
Confidence: **Confirmed**

Runtime and build dependencies use lower bounds rather than a reviewed lock with hashes. Development bootstrap upgrades pip and resolves current packages. The production package builder installs mutable PyInstaller, pip, setuptools, PyObjC, and project dependencies from the network at release time. GitHub Actions use floating major tags rather than immutable action commit SHAs.

The same SidePulse commit can therefore produce different executables on different days.

Required fix:

- create platform-specific, hash-locked dependency inputs;
- pin PyInstaller, PyObjC frameworks, build, setuptools, ruamel.yaml, and test tools;
- record the Python distribution and SDK used for release;
- pin every GitHub Action by commit SHA;
- generate an SBOM and dependency inventory;
- run vulnerability and license checks;
- build in a clean, repeatable environment with no undeclared network fetches;
- publish provenance and checksums.

### SP-AUD-106: release scripts can publish the wrong source state

Severity: **High**  
Confidence: **Confirmed**

The release script checks tracked diffs but does not reject untracked files. It does not prove local `main` equals `origin/main`. It pushes the tag before completing GitHub Release creation, so an upload failure can leave a release tag without artifacts and make a safe retry awkward.

Required fix:

- require `git status --porcelain` to be empty, including untracked files;
- fetch and require exact parity with the reviewed remote commit;
- build from an immutable detached commit or clean worktree;
- stage artifacts and verify all hashes before creating or pushing the final tag;
- make release creation resumable and idempotent;
- sign tags or use GitHub’s verified release flow;
- attach SBOM, provenance, verification manifest, and checksums.

### SP-AUD-107: installer success can hide failed setup

Severity: **High**  
Confidence: **Confirmed**

The package postinstall script suppresses failures from sleep-helper installation and user hook/LaunchAgent setup with `|| true`. It can report a successful package while leaving the application unconfigured. It also uses `ln -sfn` for `/usr/local/bin/sidepulse`, which can replace an existing user-managed path without proving ownership.

Required fix:

- distinguish required and optional setup steps;
- fail the package transaction for required setup failure;
- record optional failures and surface them on first launch;
- install a CLI link only when absent or already owned by SidePulse;
- add rollback logic;
- validate the installed sudoers helper’s contents, owner, and mode rather than checking existence only;
- provide a supported uninstall/cleanup command for helper, LaunchAgent, hooks, CLI link, and generated state.

### SP-AUD-108: main is unprotected and CI is manual-only

Severity: **High**  
Confidence: **Confirmed**

The `main` branch has no protection and required status checks are off. Test and publish workflows are manual. Portable verification covers a small rescue subset. Full tests run only on a manually invoked macOS Python 3.13 job.

This allows unreviewed or unverified changes to land directly on the release branch.

Required fix:

- protect `main`;
- require pull requests, reviewed changes, signed or verified commits where practical, and linear or intentionally merge-based history;
- require a fast Linux/static gate and a macOS gate for merge;
- use a self-hosted Mac runner if hosted credits are unavailable;
- add Python 3.10 through 3.13 compatibility coverage until the support floor changes;
- separate lint, unit, integration, package, and hardware certification results;
- block release when the exact commit lacks a completed release manifest.

### SP-AUD-109: webhook delivery has weak privacy and network policy

Severity: **High**  
Confidence: **Confirmed product risk**

Webhook settings accept both HTTP and HTTPS. Delivery follows normal URL handling and redirects, has no destination policy, and may reach loopback, private, link-local, or metadata-style addresses. Payloads include provider and session labels. Exception text is written to application logs and may contain sensitive URL material.

The URL is user-configured, so this is not presented as an unauthenticated remote exploit. It is still an unsafe default for a feature that can contain credentials in URLs and send local work metadata.

Required fix:

- default to HTTPS and require an explicit advanced acknowledgement for cleartext HTTP;
- reject credentials in URLs;
- resolve and validate every redirect destination;
- block loopback/private/link-local destinations unless an explicit local-integration mode is enabled;
- bound request and response sizes;
- redact URLs and exception details in logs;
- provide a payload preview and per-event privacy disclosure;
- make session labels opt-in or pseudonymous.

### SP-AUD-110: LaunchAgent lifecycle can hang or restart-storm

Severity: **High**  
Confidence: **Confirmed risk**

The launch plist uses unconditional `KeepAlive` but no explicit `ThrottleInterval`. `launchctl bootstrap`, `kickstart`, `print`, and `bootout` calls lack subprocess timeouts. A startup crash can create a rapid restart cycle and large logs; a stuck `launchctl` call can block setup or recovery indefinitely.

Required fix:

- add an explicit restart throttle and crash-loop circuit;
- bound every `launchctl` invocation;
- keep a startup-failure counter and safe-mode launch;
- cap and rotate stdout/stderr logs;
- expose last exit reason and restart count in diagnostics;
- test broken settings, missing helper, corrupt state, and failed migrations without a restart storm.

### SP-AUD-111: broad exception swallowing weakens recovery and observability

Severity: **High**  
Confidence: **Confirmed**

The legacy controller contains dozens of broad `except Exception` paths. Some are appropriate fail-open watcher boundaries. Others convert programming defects, invalid state, and AppKit failures into silence or generic “unavailable” status.

Required fix:

- define a small exception taxonomy for provider availability, user denial, stale data, validation, I/O, cancellation, timeout, and programming defects;
- catch only expected errors at each boundary;
- log stable reason codes, not private payloads;
- escalate invariant violations to diagnostics and crash reporting in development;
- add bounded circuit breakers instead of quiet infinite retries;
- keep user-facing copy separate from diagnostic detail.

### SP-AUD-112: documented product claims exceed the implementation

Severity: **High**  
Confidence: **Confirmed**

The approved production specification explicitly says implementation is pending. Current gaps include:

- no T3 Code compatibility adapter;
- no CodexBar bridge;
- no native SwiftUI/AppKit production host;
- no versioned core-helper protocol;
- no Animation Studio 2 or layered compositor;
- Claude capacity remains deliberately gated;
- quota-runway display constants and rendering exist, but settings force the mode back to Agent;
- spend tracking and several provider waves are not started;
- README claims about ordinary CI, subprocess-free main-thread refresh, and some display behavior no longer match reality.

Required fix:

Maintain a feature matrix with `implemented`, `experimental`, `gated`, and `planned` states. Product copy, README, screenshots, and release notes must be generated or reviewed against that matrix. A planned feature must not appear as shipped behavior.

## Medium-priority findings

### SP-AUD-201: weather network reads are unbounded and location freshness is weak

`weather_watch._get_json()` reads the complete response without a byte ceiling. IP-derived location is cached for the process lifetime, so network or physical-location changes can leave severe-weather monitoring pointed at the wrong area until restart.

Add a response bound, strict content type and schema checks, TTL-based location freshness, network-change invalidation, and visible provenance. Keep the feature off by default.

### SP-AUD-202: broad hardened-runtime entitlements need proof

The production entitlements include `com.apple.security.cs.allow-jit` and `com.apple.security.cs.allow-unsigned-executable-memory`. These materially weaken hardened-runtime protections. They may be required by the Python/WASM packaging stack, but that requirement must be demonstrated on the final artifact.

Remove every entitlement that is not necessary. Sign nested code inside-out with the minimum entitlements per executable. Do not use one broad entitlement set with `codesign --deep` as the primary signing strategy.

### SP-AUD-203: no static type, coverage, dependency, secret, or SBOM gate

Ruff and pytest are useful but insufficient for this architecture. Add:

- Pyright or mypy on all new and extracted modules;
- coverage thresholds by domain, with branch coverage for authority and safety code;
- dependency vulnerability and license checks;
- secret scanning;
- SBOM generation;
- architecture and import-boundary checks;
- property and mutation tests for parser, settings, and policy code.

### SP-AUD-204: test organization hides gaps

`tests/test_sidepulse.py` exceeds 1 MB and contains hundreds of tests. Large test files make ownership unclear, fixtures too broad, and missing seam tests hard to notice. The named resting-glow “round trip” test is an example: its name implies persistence coverage that it does not provide.

Split tests by domain and boundary. Name tests after the exact contract. Add a test inventory that distinguishes unit, contract, process integration, AppKit, packaging, installed upgrade, hardware, and performance gates.

### SP-AUD-205: giant source files remain the dominant maintenance risk

Approximate current sizes include:

- `status_bar_legacy.py`: 752 KB;
- `settings_window.py`: 209 KB;
- `colors.py`: 143 KB;
- `virtual_device.py`: 140 KB;
- `collector.py`: 118 KB;
- `install.py`: 101 KB.

Do not split files mechanically. Extract stable responsibilities with explicit interfaces and delete the old path in the same wave. No new deterministic behavior should enter the legacy controller.

### SP-AUD-206: supported Python versions are not continuously tested

The package declares Python 3.10 or newer, while the normal full workflow uses Python 3.13. Either test the supported range or raise the minimum. A compatibility promise without a matrix is not reliable.

### SP-AUD-207: repository governance is incomplete

The tree has no visible `SECURITY.md`, dependency update configuration, code-owner policy, or release support policy. Add:

- vulnerability reporting and supported-version policy;
- dependency update automation that opens reviewed PRs rather than auto-merging;
- code ownership for device safety, packaging, credentials, and settings migrations;
- release and deprecation policy;
- threat model for local IPC, webhooks, T3, CodexBar, remote peers, and helper processes.

### SP-AUD-208: diagnostics are not yet sufficient for production support

The code contains valuable bounded metrics, reason codes, and “Why is it doing that?” work. It still lacks one durable performance and health surface covering:

- menu-open P50/P95;
- longest main-thread task;
- provider event-to-visible latency;
- worker queue depth and replacements;
- Screen Bar requested versus delivered FPS;
- hardware write P95 and dedup rate;
- helper and integration versions;
- migration state;
- restart count and last exit;
- last-known-good age per source.

Implement the diagnostics page described by the approved production specification and a redacted support bundle.

## Positive controls to preserve

The audit found several areas that should be treated as reference implementations rather than rewritten casually.

### Private persistence

`private_io.py` uses owner-only permissions, no-follow opens, stable parent identities, unique scratch files, fsync, verification, and rollback. New stores should use this boundary instead of writing directly with `Path.write_text()`.

### Physical-device writes

`device_writer.py` defends against symlinks, hardlinks, parent replacement, target replacement, scratch replacement, partial writes, and mismatched readback. The missing grammar gate should be added without weakening these properties.

### Local cloud ingest

`cloud_ingest.py` is loopback-only, secret-authenticated, strict about duplicate JSON keys and non-finite values, bounded by request size, queue depth, concurrency, sessions, and rate, and careful about Host/Origin handling.

### Remote peers

`remote_peers.py` uses read-only SFTP, does not execute remote commands, bounds data and session counts, uses deadlines and circuit breakers, and prevents remote data from gaining local navigation or capacity authority.

### Credentials

`credentials.py` centralizes secret reads, prevents background Keychain prompts, persists denial cooldowns, redacts representations, bounds auth-file reads, and does not put secrets into diagnostics.

### Capacity authority

Capacity evidence is mapped onto declared lanes, filtered through authority, freshness, applicability, and reset-continuity rules before it reaches consumers. Future CodexBar and T3 data should feed this architecture rather than bypass it.

### Navigation

Navigation candidates are bounded, generation-fenced, freshness-checked, authority-checked, provider-specific, and canonicalized before execution. Keep repository mutations outside SidePulse unless a future capability model explicitly and safely permits them.

### Packaged-bundle inspection

`packaging/verify_macos_app.py` performs meaningful fail-closed checks on dependencies, imports, rpaths, symlinks, hardlinks, bundle structure, and signatures. Reproducible inputs and inside-out signing should strengthen this existing gate.

## Product-goal assessment

| Product promise | Current state | Required completion |
| --- | --- | --- |
| Ambient agent attention | Substantially implemented | Fix boundary correctness and prove latency/reliability |
| SidePulse Dot/Pro output | Feature-rich and hardened I/O | Add universal parser and safety compiler, certify hardware |
| Screen Bar | Advanced implementation | Prove performance, eliminate off-main AppKit, support all displays cleanly |
| Multi-agent and subagent awareness | Mature canonical modeling | Add seam and reachability tests |
| Multi-Mac visibility | Read-only path exists | Certify stale/offline behavior and diagnostics |
| Capacity awareness | Codex path exists; Claude gated | Integrate CodexBar, finish declared lanes, source attribution |
| T3 compatibility | Not implemented | Build capability-driven T3 adapter preserving underlying provider |
| CodexBar integration | Not implemented | Supervised structured bridge, last-known-good, source freshness |
| Creative animations | Existing DSL and editor foundations | Central safety compiler and Animation Studio 2 |
| Production-native UI | Not implemented | Incremental native host over versioned core protocol |
| Production distribution | Package tooling exists | Reproducible build, required CI, installed-upgrade and hardware evidence |
| Privacy-first behavior | Strong local defaults | Harden webhook/network policy and support disclosures |

## Ordered remediation program

### Wave 0: release freeze and confirmed blockers

Branch: `fix/production-release-blockers`

Complete before feature work:

1. move battery probing off-main and add timeouts;
2. introduce universal flash-safety compilation;
3. fix `resting_glow` serialization and persisted-field coverage;
4. remove off-main AppKit access from hardware workers;
5. implement settings schema negotiation and migration fixtures;
6. parser-gate exact final LED bytes;
7. add macOS release-evidence tooling;
8. correct README and feature-state claims.

Exit gate: complete clean macOS test, signed package, and real hardware pass.

### Wave 1: scoped runtime and performance evidence

Branch: `perf/scoped-state-deltas`

1. add signposts and performance counters;
2. introduce canonical domain deltas;
3. move transcript, battery, display, calendar, reminder, and weather observation behind workers;
4. eliminate routine `refresh_()` calls;
5. patch menus and settings by stable identity;
6. add performance budgets to the release gate.

Exit gate: meet the production specification’s P95 and idle-CPU targets on the owner’s Mac.

### Wave 2: persistence, reachability, and controller extraction

Branch: `core/typed-boundaries`

1. settings schema v2 and migrations;
2. call-reachability tests;
3. decide or delete dormant delivery planning;
4. replace `settings_window._install()` with explicit protocols;
5. extract menu projection, settings projection, render context, notification orchestration, and runtime lifecycle;
6. enable lint and static typing on every extraction.

Exit gate: legacy-controller and global-injection debt only decrease.

### Wave 3: reproducible build and repository governance

Branch: `build/reproducible-release`

1. lock dependencies with hashes;
2. pin GitHub Actions by SHA;
3. protect `main` and require checks;
4. create fast static and self-hosted macOS merge gates;
5. add dependency, license, secret, coverage, type, and SBOM gates;
6. harden installer transaction and CLI-link ownership;
7. make release creation clean, remote-verified, resumable, and provenance-backed;
8. minimize entitlements and sign inside-out;
9. add `SECURITY.md`, code ownership, threat model, and support policy.

Exit gate: rebuilding the same commit from the declared release environment produces the same dependency graph and explainable artifact differences.

### Wave 4: CodexBar and T3 integration

Branches:

- `integration/codexbar-readonly`
- `integration/t3code-compatibility`

Use the approved capability SDK. CodexBar owns provider authentication and accounting. T3 remains an orchestration surface, not a fake provider. Preserve source, provider driver, provider instance, project, thread, turn, branch, worktree, child-agent, request, and pull-request identity.

Exit gate: both integrations fail visibly and safely when absent, stale, incompatible, or unauthorized, and never block UI launch.

### Wave 5: native host and Animation Studio 2

Branches:

- `ui/native-glance-command-center`
- `studio/animation-v2`
- `ui/native-settings-diagnostics`

Build the native host incrementally over the same core protocol. Keep TCC-sensitive UI services in the signed host. Use a layered semantic animation compositor and compile to each surface through the universal safety boundary.

Exit gate: native parity, migration, rollback, accessibility, performance, and signed installed-upgrade tests pass before retiring the PyObjC host.

## Immediate issue list

The first implementation PR should contain only these confirmed fixes and tests:

- [ ] Move battery `ioreg` to a bounded worker.
- [ ] Clamp escalation takeover, previews, setup demo, Studio, and test signals.
- [ ] Add saturated-red safety policy.
- [ ] Serialize `DeviceDisplaySetting.resting_glow`.
- [ ] Add encoder/decoder field-coverage tests.
- [ ] Stop hardware workers from touching AppKit.
- [ ] Negotiate settings schema versions and fail safe on future versions.
- [ ] Parse exact final LED program bytes before every hardware write.
- [ ] Add a release-evidence command and mark current `main` unreleased.

No T3, CodexBar, new animation, or UI feature should be added before this list is green on macOS.

## Production-release checklist

A release is production-ready only when all boxes are true for the exact tagged commit.

### Correctness and safety

- [ ] All release blockers in this audit are closed with regression tests.
- [ ] Every hardware program passes the packaged firmware parser after final transformation.
- [ ] Every visible animation passes the flash-safety compiler.
- [ ] Settings migration and rollback fixtures pass.
- [ ] No AppKit access occurs off-main.
- [ ] No blocking I/O occurs on the main thread.

### Quality gates

- [ ] Ruff and static type checking pass.
- [ ] Unit, contract, process, AppKit, package, installed-upgrade, and hardware suites pass.
- [ ] Coverage and mutation thresholds pass for safety and authority modules.
- [ ] Dependency, license, and secret checks pass.
- [ ] SBOM and provenance are generated.

### macOS and hardware

- [ ] Warm launch, menu, settings, and Screen Bar budgets pass under Instruments.
- [ ] Notch, external, non-notch, sleep/wake, lock/unlock, Focus, Low Power, and accessibility scenarios pass.
- [ ] SidePulse Dot and Pro pass connect, write, dedup, readback, eject, reconnect, and malformed-program prevention tests.
- [ ] LaunchAgent crash-loop and safe-mode tests pass.

### Distribution

- [ ] Dependencies and actions are pinned.
- [ ] `main` is protected and the exact commit has required checks.
- [ ] Build starts from a clean, remote-verified commit.
- [ ] Nested code is signed inside-out with minimum entitlements.
- [ ] Developer ID, TeamIdentifier, notarization, stapling, and Gatekeeper checks pass.
- [ ] Install-over-old-version and uninstall/cleanup paths pass.
- [ ] GitHub Release includes checksums, SBOM, provenance, and verification manifest.

### Product honesty

- [ ] README and UI label every feature as implemented, experimental, gated, or planned.
- [ ] T3 and CodexBar claims match shipped capability negotiation.
- [ ] Privacy disclosures match webhook, weather, remote, credential, and diagnostic behavior.
- [ ] No planned production-spec feature is described as already shipped.

## Final assessment

The project should not be rewritten wholesale. Its strongest pure and security-sensitive modules are worth preserving. The correct path is the approved strangler architecture, beginning with the release blockers and performance boundary.

The next best engineering decision is also the least glamorous one: freeze feature expansion, make every light safe, make every setting durable, make the main thread boring, make the build reproducible, and prove the installed artifact on the actual Mac and hardware. Once that foundation is real, T3, CodexBar, native UI, and richer animations can be added without multiplying the current failure surface.
