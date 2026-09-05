# macOS installer

The signed and notarized PKG is JR-Bar's authoritative installer and manual
recovery artifact.
The release script builds the compatibility-named `SidePulse.app`, signs it
with hardened runtime, wraps it in the signed PKG, submits it to Apple's notary
service, and staples the ticket.

For production builds the builder also retains the JSON submission response,
the Apple notarization log, and the pre-staple PKG digest under
`build/macos-pkg/release-evidence-raw/`. The authoritative release gate checks
the log submission ID, accepted status, archive name, and SHA-256 before it
embeds the bounded receipt in `dist/release-verification.json`. The keychain
profile name and credentials are never written to release evidence.

Requirements:

- macOS 11.0 or newer for the current arm64 bundle
- Developer ID Application and Developer ID Installer certificates
- Xcode command-line tools
- A notarytool keychain profile
- The dedicated Sparkle signing key in the login Keychain under
  `io.sidepulse.app`

Store notarization credentials once:

```sh
xcrun notarytool store-credentials sidepulse-notary \
  --apple-id you@example.com --team-id TEAMID
```

The omitted password is requested through notarytool's secure interactive
prompt and is not exposed in process arguments.

Build, sign, notarize, and staple:

```sh
APP_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
INSTALLER_SIGN_IDENTITY="Developer ID Installer: Your Name (TEAMID)" \
NOTARY_PROFILE="sidepulse-notary" \
SPARKLE_KEY_ACCOUNT="io.sidepulse.app" \
./packaging/build_macos_pkg.sh
```

The resulting installer is written to `dist/`. On installation it places the
app in `/Applications` and links the `sidepulse` command into `/usr/local/bin`.
Hooks, per-user LaunchAgents, privileged helpers, and provider integrations are
configured only through JR-Bar's explicit first-run or CLI actions.

The production builder embeds pinned Sparkle 2.9.6, notarizes and staples the
app before final packaging, and creates the supplemental
`SidePulse-<version>-<architecture>.zip`. The authoritative release gate signs
`appcast.xml` with the Keychain-held key, writes candidate-bound channel
metadata, and verifies nested Sparkle code, app notarization and stapling, the
ZIP, and the signed feed. Unsigned local packaging intentionally creates
neither the updater ZIP nor updater evidence.

The final PKG assembly stage and release-checksum writer are exercised through
`make fast` with isolated executable doubles. Those tests prove exact command
construction, artifact naming, deterministic hashes, and clear missing-tool or
certificate failures. They do not invoke Developer ID identities, notarytool,
Gatekeeper, Installer, publication, or physical hardware, and they do not make
a release-readiness claim.

For local packaging verification when release certificates are unavailable,
run `ALLOW_UNSIGNED=1 ./packaging/build_macos_pkg.sh`. That output is explicitly
not suitable for distribution and cannot be notarized.
