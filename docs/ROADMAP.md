# JR Bar roadmap

Living roadmap, updated 2026-08-30. JR Bar is the product name; the
`sidepulse` CLI, bundle identifiers, support paths, and SidePulse hardware
names remain stable until a separately tested migration exists.

This is a navigation document, not a second evidence ledger. Statuses and
receipts live in the linked [feature and readiness matrix](FEATURE-MATRIX.md),
[completion contract](superpowers/plans/2026-08-28-jr-bar-completion-contract.md),
and tranche plans. The roadmap's terminal boundary is the 72-item master
roadmap, completed only when source, installed-app, hardware, accessibility,
privacy, performance, packaging, and release evidence are all attributable.

## Now

### P5.67–P5.72 integration and source-level closeout

Integrate the roadmap, provider/effect authoring guidance, issue templates,
community standards, data-only gallery, and bounded ecosystem refresh.
Artifacts for the 72 recommendations now exist as reachable source
implementations or governing documents. Source reachability includes the
versioned local API plus a bounded Waybar client (47), data-only Effect and
Scene packs with import, preview, export, duplicate, and rename paths (48), the
native Effect Studio and Preview Lab with scoped assignments and guarded
single-writer hardware previews (50), the shared ambient compiler and existing
Screen Bar and hardware worker sinks (49–66), the native and CLI data-only
gallery (71), and the manual research cadence (72). Item 46 is source-complete
for an explicit glance-only private listener and a foreground iOS verifier/client
with a manual test and active-scene refresh. Real private-LAN, iPhone/iPad,
permission, and rendered-device proof remain external, as do community pack
publication and installed-app behavior.
The [master roadmap design](superpowers/specs/2026-08-28-jr-bar-master-roadmap-and-ambient-effects-design.md)
remains the recommendation authority, while the [feature matrix](FEATURE-MATRIX.md)
and [completion contract](superpowers/plans/2026-08-28-jr-bar-completion-contract.md)
remain the evidence authorities.

This is no longer source-only. A local 0.6.0 app candidate was Developer ID
signed, accepted by Apple's notary service, stapled, accepted by Gatekeeper,
copied into the user Applications directory, and observed running from the
same app-tree bytes. One connected SidePulse device also passed a bounded,
reversible write and restore smoke test. Those receipts do not close the outer
installer, package receipt, installed UI, accessibility, permissions, Screen
Bar Instruments, Dot, two-candidate updater, publication, or release gates.

P5.72's recurring review is the manual, source-controlled
[upstream research cadence](UPSTREAM-RESEARCH-CADENCE.md). Its current dated
reference is the [2026-08-30 refresh](UPSTREAM-REFRESH-2026-08-30.md); the
cadence's stale-ledger rule governs when that reference must be refreshed.

## Next

### External release evidence

Finish the remaining installed UI and Screen Bar checks, accessibility and
permissions, Instruments performance capture, Dot coverage, Developer ID
Installer signing, package notarization and receipt installation, two-candidate
updater upgrade and downgrade behavior, and publication authority. P1.23 and
P2.32 remain explicitly gated by the
[acceptance gates](superpowers/plans/2026-08-28-jr-bar-completion-contract.md#acceptance-gates)
and [stop rule](superpowers/plans/2026-08-28-jr-bar-completion-contract.md#stop-rule).

### Three adapt-next ideas from the 2026-08-30 refresh

After the external evidence boundary is understood, evaluate the refresh's
bounded ideas: a monotonic transcript-discovery deadline, per-source freshness
propagated across every publication surface, and an optional presentation-only
privacy mode. Each needs a local contract and evidence before adoption. See
[UPSTREAM-REFRESH-2026-08-30.md](UPSTREAM-REFRESH-2026-08-30.md).

## Later

- **Sleep/wake reacquisition:** evidence-gated reproduction on the real Pro,
  including USB re-enumeration, reconnect, and readback.
- **Progress coalescing:** profile first, then consider a write-floor or
  trailing-edge change only if it improves durability without delaying urgent
  cues.
- **Broader remote observation:** only after demonstrated need, authenticated
  bounded transport, stale-aware behavior, and content-minimized read-only
  evidence exist.

## Adaptation Ledger

Statuses are deliberate dispositions, not implementation claims. `adopted`
means the behavior or discipline is used substantially as described;
`adapted` means it was reshaped for JR Bar's architecture or safety rules;
`surpassed` means JR Bar already has a stronger or more complete local path;
`rejected` means the approach conflicts with a locked boundary; `waiting on
evidence` means reachability, hardware, schema, performance, or security proof
is still missing.

| Source or idea | Status | JR Bar disposition and evidence |
| --- | --- | --- |
| Upstream isolated user installer | adopted | Ported as `scripts/install-user.sh` while sealed app-bundle and signed-package paths remain authoritative. [Upstream sync](UPSTREAM-SYNC.md) |
| Upstream packaging, clean-install, version, and post-build guards | adopted | Carried through package-contract tests, clean-install validation, release-version checks, and checksums. [Upstream sync](UPSTREAM-SYNC.md) |
| Upstream hook stability and compatibility | adopted | Fail-open current and legacy hook entry points are part of the fork's intake contract. [Feature matrix](FEATURE-MATRIX.md#agents-and-intake) |
| Upstream PR #31 display-sleep-safe keep-awake | adapted | JR Bar keeps display assertion opt-in and separates ordinary, battery, and closed-lid policy. [Upstream sync](UPSTREAM-SYNC.md) |
| Upstream PR #32 hook-latency path | adapted | One bounded FIFO preserves ordering, tracked children, overload receipts, and shutdown drain instead of detached processes. [Upstream sync](UPSTREAM-SYNC.md) |
| Upstream remote observation PR #27 | waiting on evidence | The literal host-management surface is not adopted. Evaluate only authenticated, bounded, stale-aware, content-minimized read-only observation after demonstrated need. [2026-08-30 refresh](UPSTREAM-REFRESH-2026-08-30.md#original-upstream-prs) |
| Upstream custom terminal selection and status-bar patches | surpassed | JR Bar has fork-native terminal routing and controller architecture; behavior must be ported only with a local regression seam. [Upstream sync](UPSTREAM-SYNC.md) |
| Upstream ScriptingBridge dependency | rejected | No import or reachable defect requires it; adding it would add install weight without repairing behavior. [Upstream sync](UPSTREAM-SYNC.md) |
| CodexBar refresh, OAuth, Keychain, and redirect-safety patterns | adopted | Refresh taxonomy, pre-emptive refresh, item-attribute change detection, cadence discipline, and same-origin refusal are reimplemented locally. [Prior art](PRIOR-ART.md#codexbar) |
| T3 provider-instance, receipt, projector, and drainable-worker patterns | adapted | JR Bar uses bounded read-only T3 projection and local receipt semantics without importing T3's controller or cloud assumptions. [Ecosystem research](ECOSYSTEM-RESEARCH.md#t3-code-pingdotggt3code-provider-tech-to-watch) |
| T3Notch approvals, grouped cards, and activity context | adapted | Words belong in the announcer and Agent Browser; LEDs remain compact semantic cues. [Ecosystem research](ECOSYSTEM-RESEARCH.md#t3notch-zortos293t3notch-announcer-surface-ideas) |
| Arbitrary executable effect plugins | rejected | Data-only effect packs may be considered later; executable plugins violate safety, privacy, and stability boundaries. [Master design](superpowers/specs/2026-08-28-jr-bar-master-roadmap-and-ambient-effects-design.md#considered-approaches) |
| Independent per-surface effect implementations | rejected | One semantic effect system is the locked architecture so surfaces do not drift. [Master design](superpowers/specs/2026-08-28-jr-bar-master-roadmap-and-ambient-effects-design.md#considered-approaches) |
| Kiro provider and other unreachable provider ideas | waiting on evidence | Port only when installed and directly reachable on the owner Mac; unreachable providers fail the reachability ratchet. [Ecosystem research](ECOSYSTEM-RESEARCH.md#worth-building-next-not-started) |
| Screen Bar JSC frame batching and cycle precomputation | waiting on evidence | Measure against the Instruments ritual and idle-motion budget before changing the renderer. [Ecosystem research](ECOSYSTEM-RESEARCH.md#worth-building-next-not-started) |
| Monotonic transcript-discovery deadline | adapted | Candidate for a bounded discovery contract, to be evaluated after source closeout and external evidence. [2026-08-30 refresh](UPSTREAM-REFRESH-2026-08-30.md) |
| Per-source freshness across every publication surface | adapted | Candidate for one freshness authority projected consistently to menu, browser, Screen Bar, and hardware, pending local contract and evidence. [2026-08-30 refresh](UPSTREAM-REFRESH-2026-08-30.md) |
| Presentation-only privacy mode | adapted | Candidate to hide presentation names and paths while retaining safe state, with canonical truth unchanged; requires explicit privacy and accessibility evidence. [2026-08-30 refresh](UPSTREAM-REFRESH-2026-08-30.md) |
| Phone companion and broader fleet/mobile surfaces | waiting on evidence | Revisit only after local security, authenticated bounded transport, and read-only behavior are proven. [Master roadmap](superpowers/specs/2026-08-28-jr-bar-master-roadmap-and-ambient-effects-design.md#p3-high-value-product-improvements) |

## Operating rules

- Reachable behavior and source-to-effect seams define done; unit coverage alone
  does not.
- Preserve canonical truth while suppressing or clearing presentation state.
- Keep words in the announcer and browser, and keep physical output bounded,
  interpretable, and safe under Reduce Motion, DND, low power, sleep, and
  disconnects.
- Review upstream commit by commit. Port behavior plus its test, not a large
  controller diff. [Upstream sync policy](UPSTREAM-SYNC.md)
- Never convert source or isolated AppKit receipts into installed-app,
  hardware, signing, notarization, publication, or release claims.
- Upstream research is read-only and non-automated. It never authorizes merge,
  push, release, deploy, issue-bot, credential, permission, telemetry, or
  hardware/system mutations. [P5.72 cadence](UPSTREAM-RESEARCH-CADENCE.md)
