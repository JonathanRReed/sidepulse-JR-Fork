## Change

Describe the user-visible behavior and the exact source-to-effect path.

## Safety and privacy

- [ ] No credential, prompt, transcript, server body, stderr, private path, project title, or account identifier reaches logs, diagnostics, notifications, webhooks, fixtures, or screenshots.
- [ ] Network, subprocess, filesystem, hardware, and persistence work is bounded and off the AppKit interaction path.
- [ ] AppKit objects remain main-thread-owned.
- [ ] New light output passes the universal presentation compiler and exact firmware parser.
- [ ] New settings fields include schema, migration, round-trip, future-version, and corrupt-file coverage.
- [ ] Install, update, rollback, and uninstall behavior is explicit and reversible.

## Architecture

- [ ] New state is represented by typed facts or values.
- [ ] Refresh impact is assigned to the correct `CoreDomain`.
- [ ] Queues, caches, histories, strings, files, payloads, and retries have hard bounds.
- [ ] The historical controller and test monolith did not grow.
- [ ] Reachability is tested at the production call site, not only at the module level.

## Verification

- [ ] `./scripts/verify.sh --portable`
- [ ] `./scripts/verify.sh` on macOS for AppKit changes
- [ ] Physical SidePulse Pro/Dot test for device changes
- [ ] Signed/notarized installed-upgrade gate for release-path changes
- [ ] Instruments evidence for performance-sensitive changes

Paste the relevant command output or attach the generated evidence manifest. Do not mark a release candidate production-ready until `scripts/verify_macos_release.sh` passes.
