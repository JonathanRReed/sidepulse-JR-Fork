# JR Bar P2.32 signed Sparkle channel design

## Goal

Add a secure, user-consented Sparkle 2 update path without weakening JR Bar's
existing PKG-first release contract. The signed and notarized PKG remains the
authoritative installer and manual recovery artifact. Sparkle receives a
supplemental notarized ZIP containing the same signed `SidePulse.app` tree.

This is a source and release-tooling tranche. It does not authorize publishing
a GitHub Release, replacing the installed app, changing TCC permissions, or
claiming a live updater pass without two attributable signed candidates.

## Official dependency

- Framework: Sparkle 2.9.6
- Release archive:
  `https://github.com/sparkle-project/Sparkle/releases/download/2.9.6/Sparkle-2.9.6.tar.xz`
- SHA-256:
  `52bf9e88cdd972fc0c81501377a880e90d47031bd8ca5462488f843e2609e192`
- Exact prepared-distribution digest:
  `a57379fc39978044fe38787bda8ca8613d48bc9da48296514622be83651d17ce`.
  This binds the selected framework, tools, license, paths, symlink targets,
  file hashes, and executable bits rather than trusting a version label alone.
- License: MIT, copied from the verified distribution into the packaged app.
- Update signing: EdDSA key stored in the macOS login Keychain under a
  JR-Bar-specific account. Only the public key is checked into source.

Sparkle 2.9.6 is selected because it is the current stable release and includes
the 2.9.2, 2.9.5, and 2.9.6 updater hardening fixes. Dependency preparation
must fail closed on an archive digest mismatch, unsafe archive paths, missing
framework members, missing tools, or a version mismatch.

Primary references:

- <https://sparkle-project.org/documentation/>
- <https://sparkle-project.org/documentation/programmatic-setup/>
- <https://sparkle-project.org/documentation/publishing/>
- <https://sparkle-project.org/documentation/security-and-reliability/>
- <https://github.com/sparkle-project/Sparkle/releases/tag/2.9.6>

## Artifact contract

The release contract moves to schema 3 and declares four distinct roles:

1. `SidePulse-<version>-<architecture>.pkg` is the primary and authoritative
   macOS installer.
2. `SidePulse-<version>-<architecture>.zip` is a required, supplemental Sparkle
   update archive containing only `SidePulse.app`.
3. `appcast.xml` is a required signed update feed stored in the durable
   `updates` GitHub Release.
4. `jr-bar-update-channel.json` is candidate-bound metadata recording the
   candidate ID, channel, archive and appcast SHA-256 values, public-key
   fingerprint, feed URL, download URL, and phased-rollout policy.

The updater feed never becomes the installer of record. A user can always
recover with the exact verified PKG. Wheel and sdist artifacts remain developer
artifacts and are not macOS product installers.

## Feed and channel model

JR Bar uses one durable feed URL:

`https://github.com/JonathanRReed/sidepulse-JR-Fork/releases/download/updates/appcast.xml`

The default Sparkle channel is JR Bar's stable channel. Stable items therefore
omit `sparkle:channel`, which is Sparkle's required representation of the
default channel. Beta items use `sparkle:channel` with the exact value `beta`.
The runtime can opt into beta items, but Sparkle always includes stable items.

Stable releases use a one-day phased rollout interval. Sparkle uses seven
fixed groups, so an ordinary automatic check rolls the version out across
seven days. Manual checks and critical updates bypass phased rollout by
Sparkle design. Beta releases are not additionally phased.

Each versioned ZIP is downloaded from its versioned GitHub Release. The
durable `updates` release owns only the current signed feed and channel
metadata. Publication updates that durable feed only after the versioned
release and all of its immutable assets are available.

## Runtime integration

The production bundle embeds `Sparkle.framework` at
`Contents/Frameworks/Sparkle.framework`. The existing Python/PyObjC process
loads the embedded framework through `NSBundle`, looks up
`SPUStandardUpdaterController`, and creates it on the AppKit main thread.
No new compiled JR Bar helper or Python package is required.

The controller is retained for the process lifetime. Source runs and malformed
bundles fail closed to an unavailable updater without importing or downloading
Sparkle. Runtime startup requires all of the following:

- execution from a real `SidePulse.app` bundle;
- the embedded framework at the exact reviewed path;
- `SUFeedURL`, `SUPublicEDKey`, `SURequireSignedFeed`, and
  `SUVerifyUpdateBeforeExtraction` in the main `Info.plist`;
- a successfully loaded `SPUStandardUpdaterController` class.

`SUEnableAutomaticChecks` is intentionally omitted. Sparkle therefore owns the
standard permission prompt and user preference instead of JR Bar forcing an
automatic network check. The normal Sparkle interval remains 24 hours.

The visible status-bar menu gains one `Software Update` submenu when the
runtime is available:

- `Check for Updates...`
- `Stable Updates`
- `Beta Updates`

The channel choice is stored in the app's `NSUserDefaults` domain. A small
updater delegate returns `beta` from `allowedChannelsForUpdater:` only when the
user selects beta. Changing the channel resets Sparkle's update cycle after a
short delay. It does not force a check, overwrite Sparkle's automatic-check
preference, or create a second feed URL.

## Bundle construction and signing

The package builder prepares the pinned Sparkle distribution into its isolated
build root, then copies the framework with symlinks and executable bits
preserved. It writes these exact main-bundle keys before signing:

- `SUFeedURL`
- `SUPublicEDKey`
- `SURequireSignedFeed = true`
- `SUVerifyUpdateBeforeExtraction = true`

The app is not sandboxed, so the Sparkle sandbox XPC opt-in keys and temporary
Mach lookup entitlements are not added. The current root app entitlements stay
unchanged.

Signing remains explicit and inside-out. Every nested Sparkle Mach-O, XPC,
application, and framework is signed before the root app. Signing never uses
`--deep`. Verification checks the root and nested code for strict signature
validity, hardened runtime, the same Developer ID team, reviewed empty nested
entitlements, exact framework version, safe symlinks, and bundle-contained
dependencies and rpaths.

For a production candidate, the release path:

1. signs the complete app;
2. submits an app ZIP to Apple's notary service;
3. staples and validates the app;
4. creates the final updater ZIP from the stapled app;
5. assembles, signs, notarizes, and staples the authoritative PKG from that
   same stapled app tree;
6. verifies both artifacts before candidate evidence is created.

Unsigned local builds may exercise bundle construction, but they are not
update candidates and cannot produce passing update-channel evidence.

## Appcast generation and evidence

JR Bar delegates archive and feed signing to Sparkle's pinned
`generate_appcast` and `sign_update` tools. It does not implement EdDSA or
Sparkle XML signing itself. The private key is loaded from Keychain by account
name. It is never accepted as an argv value, written to the repository, copied
into `dist/`, or included in logs and receipts.

The generator runs in an isolated staging directory containing the current
ZIP and, when available, the previous signed appcast and retained archives.
After generation, JR Bar validates:

- the final appcast and update archive pass pinned `sign_update --verify`
  against the expected Keychain public key and exact prepared distribution;
- the feed signature marker exists;
- exactly one item matches the current version and build;
- the enclosure URL names the exact versioned ZIP;
- the enclosure length matches the ZIP;
- the enclosure has an EdDSA signature;
- the stable or beta channel encoding is exact;
- the phased-rollout interval matches channel policy;
- the archive contains one `SidePulse.app` and no unrelated top-level files;
- the archive, appcast, and channel metadata hashes match the candidate-bound
  receipt and final release manifest.

The release evidence schema gains separate receipts for the update archive,
nested Sparkle signing, app notarization/stapling, and the signed appcast. A
publisher must refuse missing, duplicated, failed, stale, or byte-drifted
updater artifacts just as it already refuses a changed PKG.

## Upgrade and rollback policy

Sparkle updates are strictly monotonic by `CFBundleVersion`. An updater feed
may never install the same or an older version. The installed-upgrade gate and
release evidence validator both enforce `previous < candidate`.

JR Bar does not pretend that Sparkle has an automatic rollback API. Recovery
from a bad release uses the authoritative signed PKG through an explicit manual
operator workflow. A future recovery build must carry a new, higher build
number even if it restores older application behavior. Tests cover:

- `N-1 -> N` accepted;
- `N -> N` rejected;
- `N+1 -> N` rejected;
- stable clients ignoring beta-only items;
- beta clients accepting stable and beta items;
- tampered archive, appcast, channel, length, signature metadata, or candidate
  binding rejected.

The live acceptance gate still requires two real Developer ID signed and
notarized candidates on a disposable QA account. It must install `N`, update
to `N+1` through the staged feed, relaunch, preserve settings and code identity,
and prove that a stale or lower feed does not downgrade the app. Until that
receipt exists, source completion is not a live-update claim.

## Publication order and failure handling

`publish_release.sh` remains the only publication entrypoint. When publication
is separately authorized it must:

1. create a draft version release against the exact verified commit;
2. upload every manifest-bound immutable artifact, including the update ZIP;
3. publish the version release;
4. update the durable `updates` release with the already signed appcast and
   channel metadata;
5. report failure without deleting a successfully published version release if
   the later feed update fails.

The feed is never updated before the versioned archive exists. A feed update
failure leaves installed clients on the prior valid feed rather than pointing
them at a missing artifact.

## Completion boundary

P2.32 source implementation is complete when the runtime, menu, pinned
dependency preparation, bundle embedding, exact signing verification,
supplemental ZIP, signed appcast tooling, candidate-bound evidence, monotonic
upgrade policy, documentation, focused tests, fast gate, full stable-tree
suite, and independent review pass.

It remains an external release gate until the owner has a clean release
checkout, signing identities, notary profile, dedicated Sparkle private key,
two differently versioned candidates, and explicit publication authority.
