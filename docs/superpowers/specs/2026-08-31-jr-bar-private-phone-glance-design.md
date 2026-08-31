# JR Bar Private Phone Glance Design

## Scope

JR Bar will expose its existing content-minimized phone glance on an explicit
private-network address and the iOS companion will consume it as a read-only
status surface. The general status API remains loopback-only. This feature does
not add discovery, remote commands, background polling, public-network access,
or a new production dependency.

## Server boundary

The existing `sidepulse serve` command remains bound to `127.0.0.1`. A separate
`sidepulse glance` command requires an explicit IP literal and refuses wildcard,
unspecified, loopback, hostname, documentation, carrier-grade NAT, and public
addresses. It accepts RFC 1918 IPv4, IPv4 link-local, IPv6 unique-local, and
IPv6 link-local addresses. The listener serves only `GET /glance.json`; `/`,
`/status.json`, query variants, and every other method or path fail closed.

The listener is opt-in and reads `SIDEPULSE_PHONE_GLANCE_SECRET` from the
environment. The secret never appears in CLI arguments, logs, persisted state,
or the response. This HMAC authenticates the status document. Plain HTTP on a
private LAN does not provide confidentiality, so the payload remains strictly
minimized and the UI must not describe the transport as encrypted.

## Cross-platform signed envelope

The current readable envelope fields remain `source_id`, `sequence`,
`observed_at`, `payload`, and `signature`. The encoded network envelope adds
`signed_body`, a bounded base64url encoding of the exact canonical JSON bytes
that the Python signer received. Python and Swift verify the HMAC over those
decoded bytes instead of reconstructing JSON. Both implementations then parse
the signed body and require its source, sequence, time, and payload to equal the
readable outer fields. This closes float-format and escaping differences without
removing the existing readable contract.

Python-created in-memory envelopes without `signed_body` remain valid for local
compatibility, but network encoding always emits `signed_body`. The iOS client
requires it. The complete encoded response remains capped at 8 KiB.

## iOS client boundary

The iOS companion adds two focused units:

- `PhoneGlanceContract.swift` owns endpoint validation, strict envelope parsing,
  base64url decoding, HMAC-SHA256 verification, signed-body equality, freshness,
  future-skew, and persistent sequence replay rules.
- `PhoneGlanceClient.swift` owns one ephemeral `URLSession` request to the fixed
  `/glance.json` path with a five-second timeout, no redirects, an 8 KiB body
  ceiling, and generic failures that never include response bodies or secrets.

The endpoint is configured as a private IP literal and bounded port. The shared
glance secret uses a dedicated Keychain item, separate from the existing APNs
proxy secret. The last accepted sequence is stored in `UserDefaults`, keyed by
source ID, and advances only after every verification step passes.

## App and UI behavior

`AppModel` owns saved host and port values, the protected secret, refresh state,
the last verified snapshot, and the sequence checkpoint. It refreshes only when
the app becomes active or the user taps Refresh. It does not add a background
mode or a timer.

The main screen adds a compact Computer Glance card showing one of these honest
states: not configured, checking, verified status with age, stale, or
unavailable. Settings add Mac private IP, port, and a SecureField for the glance
secret, plus Save and Test actions. Copy must say that the feed is signed and
read-only on the local network, not encrypted. Reduce Motion requires no special
case because this surface adds no animation.

`Info.plist` adds the local-network purpose string and allows local-network HTTP.
The app asks for local-network access only when the configured feed is fetched.

## Verification

Focused Python tests cover the isolated listener, address refusals, route
isolation, signed-body mismatch, tampering, replay, expiry, and deterministic
Python-to-Swift fixtures. A Darwin-only test compiles and executes the pure Swift
contract against a Python-generated signed fixture. The iOS source is typechecked
with the available Swift compiler. Full Xcode build and device UI verification
remain external because this Mac currently has Command Line Tools selected, not
full Xcode.

## Non-goals

- No Bonjour or automatic peer discovery.
- No remote LED writes or command channel.
- No background fetch, push replacement, or continuous polling.
- No public hostname, wildcard bind, VPN assumption, or cloud relay.
- No promise of LAN confidentiality.
