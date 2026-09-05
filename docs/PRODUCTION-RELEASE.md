# JR-Bar production release gate

A release tag may be published only after the authoritative macOS gate passes from a clean `main` checkout that exactly matches `origin/main`.

## Required evidence

Set the signing identities and notarization profile used by `packaging/build_macos_pkg.sh`:

```bash
export APP_SIGN_IDENTITY='Developer ID Application: …'
export INSTALLER_SIGN_IDENTITY='Developer ID Installer: …'
export NOTARY_PROFILE='sidepulse-notary'
export SPARKLE_KEY_ACCOUNT='io.sidepulse.app'
export SIDEPULSE_RELEASE_CHANNEL='stable'
```

## Authoritative artifact

The signed and notarized PKG remains the authoritative installer and manual
recovery artifact:

```text
dist/SidePulse-<version>-<architecture>.pkg
```

The filename keeps the SidePulse compatibility name because the package
installs `SidePulse.app`, the `SidePulse` executable, and the `sidepulse` CLI.
The application displays JR-Bar to people.

Production packaging also creates a required supplemental Sparkle ZIP from the
same signed, notarized, and stapled `SidePulse.app`. The signed `appcast.xml`
and `jr-bar-update-channel.json` bind that ZIP to the exact candidate. They do
not become a second installer contract.

Production packaging requires both Developer ID identities and a notarytool
profile. Store the notarization credentials once. Omit `--password` so
notarytool reads the app-specific password from its secure interactive prompt
instead of placing it in process arguments:

```bash
xcrun notarytool store-credentials sidepulse-notary \
  --apple-id <your-apple-id> --team-id AJ9VWBRNZN
```

JR-Bar embeds pinned Sparkle 2.9.6 and exposes `Software Update` only from a
complete packaged bundle. Sparkle owns its first automatic-check consent
prompt because the app deliberately omits `SUEnableAutomaticChecks`. Stable is
the default channel and uses a one-day phased rollout interval. Beta is an
explicit opt-in channel. No JR-Bar GitHub Release or update feed has been
published by this source tranche.

The prepared Sparkle framework, helpers, tools, and license must match exact
distribution digest
`a57379fc39978044fe38787bda8ca8613d48bc9da48296514622be83651d17ce`.
The final signed-appcast receipt reruns pinned `sign_update --verify` against
both the appcast and update archive. A matching version string alone is not a
release provenance receipt.

The dedicated Sparkle private key is stored in the owner's login Keychain under
account `io.sidepulse.app`. Only its public key is committed. Before a real
release, export one encrypted offline backup with pinned Sparkle's
`generate_keys --account io.sidepulse.app -x <secure-path>` command. Never put
that file in the checkout, shell arguments, logs, release evidence, or Git.
The committed public-key fingerprint is
`9c134249398dd15c364a29451de3d81436d8eda97a0c706fa59047e6607f59ac`.

Create an Instruments-backed JSON evidence file with these fields:

```json
{
  "warm_launch_ms": 450,
  "menu_open_p95_ms": 40,
  "pane_switch_p95_ms": 80,
  "longest_main_thread_task_ms": 12,
  "idle_cpu_hidden_percent": 0.5,
  "idle_cpu_static_bar_percent": 1.0,
  "idle_cpu_motion_percent": 2.5,
  "measurement_duration_seconds": 300,
  "instruments_trace_reviewed": true,
  "menu_tracking_io_observed": false
}
```

The measurements must come from the signed candidate on the release Mac after a five-minute warm period. Review the Instruments trace for main-thread subprocesses, filesystem walks, network requests, hardware writes, fsyncs, and unexpected AppKit work during menu tracking.

## Installer ownership

The signed package installs the application payload and an owned `sidepulse` CLI link only. Package scripts do not install provider hooks, a user LaunchAgent, the privileged sleep helper, the eject guard, or T3 Code integration. Those are external mutable state and cannot be transactionally rolled back by Installer without risking pre-existing user setup.

The ordinary user completes integrations from JR-Bar's first-run setup or explicit CLI commands. The release gate exercises the explicit installed `status-bar start` command after package installation before it checks the LaunchAgent. This keeps package installation reversible while still testing the installed integration path.

## Run the gate

Use a dedicated release Mac or disposable QA account and preserve an existing settings file for the
installed-upgrade check. A differently versioned, Developer ID signed JR-Bar
PKG must already be installed, with its package receipt present. Settings by
themselves never count as upgrade evidence. The gate deliberately exercises the supported
uninstaller, then reinstalls the exact same PKG. It preserves user settings but
removes explicitly installed integrations and helpers. Do not run that portion
against an everyday account. The default `software` profile requires no
physical SidePulse and performs no hardware writes.

```bash
export SIDEPULSE_PERFORMANCE_EVIDENCE='/absolute/path/outside-the-checkout/performance-evidence.json'
export SIDEPULSE_RUN_INSTALLED_UPGRADE=1
export SIDEPULSE_RUN_UNINSTALL=1
export SIDEPULSE_RELEASE_USER="$(id -un)"
./scripts/verify_macos_release.sh
```

To verify optional SidePulse hardware, set `SIDEPULSE_REQUIRED_HARDWARE` to
`pro`, `dot`, `both`, or `any`, connect the selected devices, and set
`SIDEPULSE_HARDWARE_CONFIRM=1`. These profiles require reversible smoke writes
and a receipt bound to the same candidate and hardware profile. A software
release emits no hardware-smoke receipt and makes no physical-hardware
validation claim.

Set `SIDEPULSE_RELEASE_CHANNEL=beta` only for a beta release. To retain prior
stable and beta entries, set `SIDEPULSE_SPARKLE_HISTORY_DIR` to an absolute
external directory containing the currently published signed `appcast.xml`
and every retained `SidePulse-*.zip` it references. The gate verifies the prior
feed and each retained archive with the pinned Keychain key before it signs a
replacement feed. Omit this variable only for the first published feed.

The gate builds first so one immutable candidate identity exists, then binds
the full source suite, clean-wheel install, performance budget, Developer ID
signatures, nested Sparkle signing, app and PKG notarization, app and PKG
stapling, Gatekeeper, the exact updater ZIP and signed appcast, package
contents, bundle closure, entitlements, requested hardware checks, installed
upgrade, settings preservation, supported uninstall, and clean PKG reinstall
to that exact candidate. The clean reinstall verifies `doctor` and
`sidepulse integrations status --json` without silently reinstalling external
integrations.

The developer-facing wheel and source distribution are rebuilt from the same
clean release checkout in an empty candidate-owned staging directory. Their
exact names come from the release-artifact contract, Twine validates them, and
the evidence manifest records their bytes and SHA-256 values. Publication
ignores unrelated files in `dist/` and refuses any asset whose bytes changed
after the evidence manifest was assembled.

Successful verification produces `dist/release-verification.json` using the
`jr-bar-release-evidence` schema. It contains the commit, final stapled PKG
hash, deterministic app-tree hash, signing identities, SBOM and performance
hashes, the selected `hardware_profile`, and one bounded receipt for every required check. Assembly fails when
a receipt is missing, duplicated, failed, malformed, secret-shaped, changed
on disk, or bound to another candidate. The pre-staple digest is checked
against the notarization log, the final PKG is independently validated by
Stapler, and the stapling receipt links both digests without pretending they
are byte-identical. `dist/performance-evidence.json` is the immutable copy included with
the release assets.

Before authorizing the release gate, enable T3 Code only when the release Mac has representative local data. Run a bounded probe and preserve the output with the release evidence:

```bash
sidepulse integrations status --json
sidepulse integrations probe t3code --json
```

An unavailable optional third-party installation is not a failure for the core package. A configured integration that is present but violates its required schema, process, freshness, or installed-command contract blocks the associated compatibility claim.

## Publish

`publish_release.sh` creates a draft release against the exact verified commit,
uploads the exact checksummed PKG, supplemental updater ZIP, performance
evidence, SBOM, candidate-bound manifest, and Python developer artifacts. It
publishes the immutable version release before changing the durable `updates`
release, then uploads channel metadata before the client-visible signed
appcast. A failure before the version release is published deletes its draft
and server-side tag. A feed-update failure leaves the prior signed appcast in
place and must be repaired before another release.

```bash
./scripts/publish_release.sh
```

Do not create or push a release tag manually. Do not treat a portable or unsigned package as a production candidate.
