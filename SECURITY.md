# JR-Bar security policy

## Supported versions

Only the newest signed and notarized JR-Bar GitHub Release is supported. Source checkouts, editable installs, unsigned packages, development app wrappers, and commits on `main` are development artifacts unless the release page contains all of the following:

- a Developer ID signed and notarized `.pkg`;
- `SHA256SUMS`;
- `sidepulse-sbom.cdx.json`;
- `release-environment.txt`;
- `release-verification.json` showing the exact commit, signing team, installed-upgrade result, hardware matrix, and performance evidence.

A version is not considered production-supported until `scripts/verify_macos_release.sh` has passed on the reviewed release Mac.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or privacy leak. Send a private report to [Contact@JonathanRReed.com](mailto:Contact@JonathanRReed.com) with:

- the affected JR-Bar version or commit;
- the macOS and hardware versions;
- a concise reproduction;
- expected and observed behavior;
- whether credentials, prompts, session labels, paths, hooks, device files, or local history may have been exposed.

Do not include real access tokens, refresh tokens, private prompts, full transcripts, or unrelated personal data. Redact secrets and use synthetic fixtures.

Reports are acknowledged as soon as they can be reviewed. A fix is published only after validation against the relevant security boundary and the complete release gate.

## Security properties that must hold

JR-Bar is a local ambient-attention utility. The following properties are release requirements:

1. **No unapproved secret access.** Keychain reads require an explicit user action. Secrets never enter logs, diagnostics, notifications, webhooks, history, or exception strings.
2. **No unauthorised remote control.** Remote-peer support is read-only. It must not execute remote commands or grant navigation, mutation, or capacity authority.
3. **Loopback is not trusted.** Local HTTP ingest is disabled by default, bearer-authenticated, loopback-only, rate-limited, concurrency-limited, and schema-bounded.
4. **No arbitrary command execution.** Navigation targets are provider-specific, canonical, freshness-checked, generation-fenced, and allowlisted before execution.
5. **Private state stays private.** Sensitive files use owner-only directories and files, no-follow descriptors, identity checks, atomic publication, bounded reads, and rollback-aware transactions.
6. **Physical writes are exact.** Device paths reject symlinks, hardlinks, mount swaps, torn writes, and readback mismatches. Exact final LED bytes must pass the packaged firmware parser before publication.
7. **Visible light is safety-compiled.** Every hardware, Screen Bar, preview, setup, test, and Studio presentation passes the universal cadence and saturated-red safety compiler.
8. **Network outputs are minimal.** Webhooks require HTTPS, public destinations, bounded payloads, no redirects, and product-owned reason codes. Session, provider, project, and user labels are removed.
9. **Build identity is stable.** Production artifacts use reviewed exact dependencies, exact entitlements, Developer ID signing, notarization, stapling, Gatekeeper checks, an SBOM, checksums, and a release evidence manifest.
10. **Upgrades do not destroy state.** Settings migrations are versioned, idempotent, backup-preserving, and read-only when a newer schema is encountered.

## In scope

- provider hook installation, preservation, and removal;
- local Unix-socket and loopback ingest;
- credential and token handling;
- settings, logs, history, export, and private-state storage;
- navigation and terminal-opening policy;
- Tailscale/SFTP peer transport;
- webhook delivery;
- hardware and power-up animation writes;
- AppKit threading and process supervision when they create security or privacy impact;
- installer, LaunchAgent, privileged helper, signing, notarization, update, and uninstall behavior;
- dependency, workflow, and release-chain compromise.

## Out of scope

- vulnerabilities in upstream AI providers, macOS, Tailscale, terminal applications, or SidePulse firmware that JR-Bar neither introduces nor can mitigate;
- denial of service requiring the user to deliberately replace reviewed binaries or disable macOS protections;
- reports based only on an unsigned development wrapper behaving differently from the signed release identity;
- social-engineering claims without a product vulnerability.

## Disclosure and remediation

Security fixes are developed on a private or restricted branch when public details would increase risk. Releases include a concise advisory, affected versions, impact, remediation, and verification evidence. Secrets found in history are revoked and removed from the repository history; merely deleting the current file is insufficient.
