# Creator Micro 2 adapter

JR-Bar has a bounded, optional adapter for the Creator Micro 2 vendor HID
collection. The wire facts come from the public
[micro-manager hacking guide](https://github.com/schacon/micro-manager/blob/main/docs/hacking.md):

- vendor ID `0x303A`, product IDs `0x8297` and `0x8298`
- usage page `0xFF00`, usage `1`
- 64-byte reports with report ID `0x06`, channel `2`, and up to 61 bytes of UTF-8
- request IDs from `0` through `999`
- responses correlated by `id`; notifications use `m` and `p`
- fragments have no sequence number or terminator, so complete top-level JSON objects are found by brace balancing that ignores quoted braces

Discovery only matches metadata. It does not open the device, send probes, or
write hardware state. JR-Bar exposes a separate output switch in Devices
settings, off by default. On first enable, JR-Bar approves only one connected
matching device with a nonempty stable serial number. It stores that identity
in the owner-private integration settings file and verifies it before every
open. Missing, identity-less, and ambiguous matches fail closed.

The serial is a stable local selection boundary, not cryptographic hardware
attestation. A counterfeit device that copies the approved serial cannot be
distinguished without a vendor authentication protocol.

Capability negotiation is a separate, explicit operation because every probe is
a HID write. It sends inert payloads for `v.oai.thstatus` and `lights.preview`
and only records a method when the firmware does not return JSON-RPC error
`-32601`. Until negotiation succeeds, the capability set stays empty. This is an
honest unsupported fallback, not a firmware-version guess.

Output is opt-in at the transport boundary. Call `enable_writes()` on
`HidApiTransport` before negotiation or output. An apply writes every fragment,
waits up to eight seconds for the exact response ID, retains interleaved input
notifications, and stops all later output after any foreign response ID. A
timeout or I/O loss closes the handle and applies a bounded reconnect delay.
`close()` is safe to call more than once.

On macOS, the transport uses `hid_darwin_set_open_exclusive(0)` and confirms
the policy through the getter exported by the pinned hidapi extension. It
applies this after device creation, immediately before opening, under a shared
JR-Bar lock. Missing APIs or an unchanged policy refuse the open. The Python
wrapper alone does not expose this option, and a seizing open is unsuitable
for the pad's shared keyboard collection. See the
[hidapi Darwin API](https://github.com/libusb/hidapi/blob/master/mac/hidapi_darwin.h).

JR-Bar owns one worker, not an exclusive hardware connection. Foreign response
IDs cause it to stop, but another client's response can collide with an
outstanding ID. The firmware supplies no authenticated ownership handshake.
Close other device controllers before enabling JR-Bar. Work Louder Input and
Codex can both communicate with the pad. A physical trial observed foreign
responses from Input; JR-Bar stopped rather than continuing writes. Complete
coexistence and sole-controller recovery checks remain open.

The production output worker performs connect, capability negotiation, and all
HID writes away from AppKit. It maps input-needed, failure, active, completion,
idle, quota warning, quota exhaustion, and reset signals to the adapter's
semantic states. If firmware does not prove `v.oai.thstatus` support, the worker
reports `unsupported_firmware` and sends no state output. A foreign response ID
reports `device_conflict`, closes the adapter, and stops later output. Enabling
Agent Deck and Creator Micro output together reports `agent_deck_ownership` and
leaves the device with Agent Deck instead of competing for it.

The same worker now polls input while lighting is idle. Each poll has a report
budget. Disconnects and foreign response IDs discard queued input. The decoder
accepts firmware envelopes without `jsonrpc`, while rejecting an explicit wrong
version. It retains notifications that share a report with an output response.

State output sends explicit per-key fields, using JR-Bar's configured colours
and shared brightness policy. An acknowledgement is not proof of visible light.
The active hardware layer must map the keys to agent codes. The setup service
can now plan and verify that change. Devices settings provides inspection,
an explicit apply preview, and guarded restore. Live UI and hardware checks
remain open.
See [Agent Deck controls](agent-deck-compatibility.md) for app actions, the
separate enable switch, and remaining hardware verification.

## Keymap setup and recovery

Setup uses the documented `device.status`, `fs.read`, and `fs.write` protocol
from the [pinned guide](https://github.com/schacon/micro-manager/blob/32caf5a429cd0618dbb8c8354e34223d601ee235/docs/hacking.md).
This is independently written compatibility code. That source snapshot has no
license file, so JR-Bar does not copy its implementation.

Inspection only reads the keymap and active layer. The planner requires an
unambiguous profile index, a valid 1-based layer index, and the documented
`[2, 4, 4, 3]` key matrix. It proposes AG00 through AG12 on that layer. Other
profiles, layers, macros, the dial, and the joystick remain unchanged. The
preview describes which normal keystrokes the agent inputs would replace.
Unsupported layouts are rejected rather than guessed.

Apply checks that the device still matches the preview, then creates an
owner-private recovery file before writing. Backup creation cannot replace an
existing file, including one created by a racing process. A readback must match
the proposed keymap before setup reports success. A firmware acknowledgement
alone is insufficient. Cancellation is checked again immediately before the
flash write.

Restore verifies the backup's device binding and content digests. It only
writes when the current device still matches the JR-Bar setup state. Later
edits made by another app cause restore to stop and preserve the backup. JR-Bar
does not edit Work Louder Input's local cache.

Keymap documents are limited to 64 KiB. Setup connections may use a separately
bounded 132,096-byte RPC envelope for the double-encoded file; ordinary lighting
connections retain the 3,904-byte limit. Fragment parsing advances once through
the incoming bytes rather than rescanning earlier fragments. Wrong-method
replies and oversized responses stop the connection.

On firmware `v0.6.1`, physical apply, restore, and reapply each passed their
readback checks. That firmware returns `id`, `method`, and `result` in replies
and can acknowledge `fs.write` with a null result. JR-Bar accepts those exact
forms without treating the acknowledgement as proof of persistence.

Real-device key presses, visible lighting, broader write-limit coverage, and
sole-controller recovery remain unverified. Bluetooth LE was the observed
transport for the successful keymap roundtrip.

`hidapi==0.14.0.post4` is BSD-3-Clause licensed. JR-Bar preserves that attribution
and links to the [upstream license](https://github.com/libusb/hidapi/blob/master/LICENSE.txt).
The signed macOS release includes its macOS arm64 CPython 3.12 wheel in the
hash-bound binary-only dependency lock. The module still imports it lazily, so
source installs on other platforms do not load the native backend unless they
use this adapter.

The adapter is deterministic under an injected transport, but actual LED output,
Input Monitoring behavior, Bluetooth framing, and coexistence with Work Louder
Input still require testing on real hardware before the integration can be
called hardware-validated.
