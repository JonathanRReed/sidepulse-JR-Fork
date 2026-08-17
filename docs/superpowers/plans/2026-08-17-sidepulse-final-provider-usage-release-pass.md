# SidePulse Final Provider Usage and Release Pass

**Goal:** Make the merged SidePulse build truthful and useful on the owner's Mac, with special focus on provider remaining-usage visibility, then perform one final repository/release audit and merge the result to `main`.

## Constraints

- Preserve the single retained production AppKit controller boundary. Do not add another PyObjC controller/facade layer.
- CodexBar remains a design/reference source only. Provider accounting is first-party SidePulse behavior.
- T3 Code remains an optional orchestration host, not a provider.
- Keep provider secrets out of logs, argv, diagnostics, settings, and exports.
- Browser-backed sources remain explicit-consent only.
- Do not consume GitHub-hosted Actions credits. Use the manual self-hosted macOS verification workflow when available.
- Do not claim macOS/AppKit, physical-hardware, signing, or live-account verification without evidence from the Mac runner.

## Task 1: Pin the provider-usage failure with regression tests

- Audit every native provider descriptor against the collector paths that are actually invoked.
- Add pure tests proving that an enabled, signed-in provider does not collapse to a generic empty reading when an app-owned source exists.
- Pin source-health reason/action behavior for missing auth, missing app data, unsupported source shape, and stale last-known-good data.
- Pin provider source fusion so dynamic quota lanes and local token/model activity can coexist in one snapshot.

## Task 2: Fix Codex/ChatGPT remaining-usage acquisition

- Trace current Codex local auth, rollout/session data, and any existing app-server/rate-limit protocol support.
- Use the strongest reviewed app-owned source that is already available in the installed environment.
- Merge remote/authoritative quota windows with local token/model counts instead of choosing one and discarding the other.
- Keep source provenance, freshness, reset time, and scoped lanes explicit.
- Fail to an actionable source-health state when a signed-in Codex installation exposes no compatible quota source.

## Task 3: Audit and repair Claude, Cursor, Devin, Grok, Antigravity, and OpenAI API acquisition

- Verify every advertised source is either implemented or removed from the capability claim.
- Ensure Claude makes the explicit Keychain connection path obvious and preserves local transcript activity even before remote quota consent.
- Ensure app-owned read-only Cursor/Grok/Antigravity sources are attempted automatically when safe.
- Ensure Devin/browser paths remain consented and actionable rather than silently empty.
- Keep unknown provider-owned quota lanes visible without granting them interruption/hardware authority.

## Task 4: Finish Usage Center and menu truth

- Make empty/provider-failure states explain the exact next action rather than `no reading` or an inert refresh.
- Ensure app launch, Finder launch, LaunchAgent, `agent-status-bar`, and source-checkout foreground entrypoints all construct the same native provider service.
- Ensure the compact root Usage row reflects native provider state and does not depend on retired CodexBar configuration.

## Task 5: Final Screen Bar and Settings audit

- Verify the rounded/bounded Screen Bar design reaches normal, compact, notchless, and Alcove-following render paths rather than only one compatibility path.
- Preserve the existing shared LED animation sampler and accessibility/reduced-motion behavior.
- Verify seven-category Settings direct navigation, Usage Center entrypoints, and explicit bracket style persistence.
- Fix only confirmed defects. Do not grow `status_bar_legacy.py` or add another settings surface.

## Task 6: Final source and release audit

- Review package entrypoints, requirements/constraints, provider resources, migrations, source-health diagnostics, process ownership, and test sandbox contracts.
- Check for temporary write-probe files, stale CodexBar runtime claims, duplicate controller definitions, and provider descriptors with no implementation.
- Keep architecture ratchets current for any new pure modules.

## Task 7: Verification and merge

- Run all verification that can be executed in the available environment.
- Dispatch the existing manual self-hosted macOS workflow for this branch if a compatible runner is available, without using hosted Actions credits.
- Inspect the final PR patch and changed-file set after the verification commit.
- Merge the final PR to `main` under the owner's explicit authorization.
- Verify the resulting `main` SHA and report any Mac-only gates that remain unproven.
