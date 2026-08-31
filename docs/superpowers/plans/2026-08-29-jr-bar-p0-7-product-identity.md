# JR Bar P0.7: Product Identity

Status: closed on 2026-08-29.

## Objective

Make JR Bar the single human-facing product name across the macOS and iOS interfaces, notifications, accessibility, CLI help, diagnostics, local status server, packaging metadata, and current user-facing documentation.

## Identity boundary

### Change to JR Bar

- Window, pane, menu, tooltip, notification, accessibility, diagnostic, and setup copy.
- CLI descriptions and human-readable command output.
- macOS and iOS display-name metadata.
- Usage Center, About, release title, current README, and current feature-matrix copy.
- HTTP `Server` display header and product-owned network user-agent labels where changing them is not a protocol contract.

### Preserve exactly

- `SidePulse.app`, the `SidePulse` executable, and `io.sidepulse.app`.
- The `sidepulse` CLI command, Python package/module names, entry points, environment variables, and state paths.
- LaunchAgent labels, Keychain services/accounts, managed-hook markers, plugin filenames, URL schemes, and installed helper paths.
- SidePulse Pro, SidePulse Dot, mount names, firmware files, and SidePulse Pro Eject Prevention.
- Historical changelog entries, archived plans, upstream attributions, and compatibility-focused error text that names the on-disk bundle or executable.
- Internal class and thread names where they are not shown to a person.

## Architecture

Add a pure `product_identity` module with `PRODUCT_DISPLAY_NAME = "JR Bar"`. User-facing source imports that constant instead of creating local spellings such as SidePulse or JR-BAR. Existing `app_bundle`, `device_identity`, and helper constants remain the authorities for compatibility and hardware identity.

The packaging flow keeps producing `SidePulse.app` with executable `SidePulse` and bundle identifier `io.sidepulse.app`, but writes `CFBundleDisplayName = JR Bar`. The iOS Info.plist follows the same display-only rule while retaining its target and bundle identifier.

## TDD sequence

1. Add a product-identity contract test that pins the display name and all preserved compatibility and hardware identifiers.
2. Update representative UI tests to require JR Bar in setup, settings, usage, menu, tooltip, and About surfaces.
3. Update notification and accessibility tests to require JR Bar and reject the old visible names.
4. Update CLI, diagnostics, server-header, macOS packaging, and iOS plist tests to require the display name while preserving compatibility names.
5. Observe the expected failures against the mixed current identity.
6. Add the pure branding module and route production surfaces through it.
7. Update current user-facing docs and the current release heading copy without rewriting historical or archived material.
8. Search production strings for remaining visible SidePulse or JR-BAR copy and classify every remaining hit as hardware, compatibility, history, or internal implementation.
9. Run focused UI, notification, accessibility, CLI, packaging, and identity tests.
10. Run the full suite, Ruff, secret scan, version validation, diff checks, and one fresh independent review.

## Completion evidence

P0.7 is complete only when:

- representative rendered and CLI surfaces show JR Bar;
- notification validation and accessibility labels use JR Bar;
- macOS and iOS display metadata use JR Bar;
- SidePulse Pro, SidePulse Dot, SidePulse Pro Eject Prevention, `SidePulse.app`, executable `SidePulse`, `io.sidepulse.app`, `sidepulse`, state paths, and service identifiers remain unchanged;
- production-string searches have no unexplained human-facing SidePulse or JR-BAR occurrence;
- focused, full, lint, secret, version, and diff gates pass;
- the independent candidate review has no unresolved identity or compatibility finding.

## Closure receipts

- The display name is centralized in Python and Swift. Standalone iOS push tools use their own shared display-name module because they run without the installed Python package.
- AppKit setup, settings, menu, tooltip, notification, accessibility, Usage Center, About, diagnostics, CLI, loopback header, macOS packaging, iOS metadata, iOS SwiftUI, Shortcuts, APNs, current docs, installer output, SBOM, and release-facing copy use JR Bar.
- SidePulse Pro, SidePulse Dot, SidePulse Pro Eject Prevention, `SidePulse.app`, executable `SidePulse`, `io.sidepulse.app`, `sidepulse`, URL schemes, managed-hook markers, state paths, and service identifiers remain unchanged.
- Focused post-review gate: 879 tests and 7 subtests passed. The complete post-review gate passed with 6,243 tests, 7 subtests, and 4 existing multiprocessing deprecation warnings.
- Ruff passed across source, tests, packaging, and scripts. The changed standalone iOS Python tools passed import, syntax, and undefined-name lint. Python compilation and Swift parser validation passed.
- The canonical secret scan passed across 571 tracked files, and the same high-confidence scanner passed separately across all 17 untracked goal files. Dependency policy, package health, release-version validation at 0.5.0, and `git diff --check` passed.
- The one independent review found two gaps: visible iOS and APNs copy, plus visible OpenClaw hook documentation. Both findings were fixed and regression-tested. The compatibility ownership marker remained unchanged. No second review was requested.
- Full Xcode build and live iOS rendering remain later installed-app evidence. This host has only Command Line Tools selected, so `xcodebuild` cannot run. The modified Swift sources passed `swiftc -parse`.
