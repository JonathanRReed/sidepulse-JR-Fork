# Agent Deck compatibility

JR-Bar includes device actions inspired by Jonathan Reed's
[Agent Deck](https://github.com/JonathanRReed/agent-deck), reviewed at commit
`4a4155fecbc86d6b487ffceb543b16119b342c62`. The Python implementation lives in
JR-Bar. It does not bundle the installed Codex or Work Louder SDK.

The built-in controls do not need an Agent Deck snapshot producer. In Settings,
open Devices, connect Creator Micro 2, and configure Agent Deck controls. Each
logical key can open an app, send one recorded shortcut to that app, reveal the
current ask, open Agent Browser, or open Usage Center. Save the mapping, then
enable device actions. The two switches separately control device access and
action execution.

App shortcuts require macOS Accessibility permission and the mapped app must
already be frontmost. Events target that app's checked process ID. Device data
can select a saved mapping, but cannot supply a command, URL, text, or target.
There are no provider approval or orchestration commands. The recorder captures
Command shortcuts before JR-Bar's menu handles them.

Mappings live in owner-private `deck-controls.json` beside `integrations.json`.
Actions are off by default. Saves reject changed, malformed, or newer settings.
Only one short-lived input batch can wait on AppKit. Actions expire after half
a second and cannot replay after disabling controls or closing the runtime.

The active hardware layer must emit AG key events. Devices settings includes
keymap inspection, a change preview, explicit apply, and guarded restore.
Setup saves a private recovery file before writing and verifies the full device
readback afterward. It configures the active key matrix while preserving other
layers, profiles, dial, and joystick mappings. See
[Creator Micro setup and recovery](creator-micro-2.md#keymap-setup-and-recovery).
Live onboarding, key/dial/joystick input, LED appearance, and coexistence still
need physical Creator Micro 2 verification. Source tests and a rendered
settings pane do not prove those.

## Optional read-only snapshot compatibility

Separately, JR-Bar can observe a local JSON snapshot based on Agent Deck's
`DeckSnapshot` type. Snapshot content never executes device actions. This
compatibility mode has no command or provider-control API and yields the device
to the external Agent Deck process.

## Snapshot contract

The current Agent Deck repository defines this contract but does not yet write
it to disk. JR-Bar does not auto-discover Agent Deck or claim that the file
exists. The integration runs only when the user enables it and configures a
snapshot path.

Version 1 accepts the public `DeckSnapshot` shape. This abbreviated example
shows one provider and session:

```json
{
  "activeProviderId": "claude",
  "device": {"connection": "wired", "owner": "native_passthrough"},
  "generation": 7,
  "providers": {
    "claude": {
      "capabilities": [],
      "connected": true,
      "providerId": "claude",
      "selectedSessionId": "agent-1",
      "sessions": {
        "agent-1": {
          "capabilities": [],
          "pinned": false,
          "providerId": "claude",
          "selected": true,
          "sequence": 1,
          "sessionId": "agent-1",
          "state": "needs_input",
          "title": "Review the plan",
          "unread": false,
          "updatedAt": "2026-09-04T15:29:30Z"
        }
      },
      "slotOrder": ["agent-1"],
      "voice": "off"
    }
  },
  "updatedAt": "2026-09-04T15:29:30Z"
}
```

Unknown fields are rejected. `agent_id` and `provider` are bounded safe
identifiers. `state` is one of `needs_input`, `error`, `running`,
`complete_unread`, `idle`, `offline`, or `unassigned`. `updatedAt` is required, must be an exact
RFC 3339 timestamp with seconds and a timezone, and must fall within the
consumer's bounded age and clock-skew window.

Navigation is separate metadata, not part of `DeckSnapshot` and not authority.
Any navigation hint must use the complete shape
`<allowed-scheme>://session/<provider_id>/<session_id>`, where the allowed
schemes are `agent-deck`, `t3code`, and `alcove`. User information, ports,
encoding, extra path segments, queries, and fragments are rejected.

JR-Bar presents observations in this explicit order: input needed, failure,
active, completion, then idle. Offline and unknown observations follow those
five states. Valid observations map to JR-Bar's canonical `AgentStatus` and
`WorkKey` identities.

## File and runtime safety

The integration is disabled by default. While disabled, it performs no file
access and starts no background worker. When enabled, the reader opens the
configured file through a no-follow descriptor, accepts only a private regular
file owned by the current user, and reads at most 1 MiB. It does not place the
configured path in receipts or errors.

A missing or inaccessible source produces an `unavailable` receipt. A source
that is present but refused, unsafe, oversized, malformed, stale, or
incompatible produces an `invalid` receipt. This distinction lets the UI be
honest without exposing local path details.

The optional snapshot service has an injected reader and clock, a bounded
polling cadence, and explicit start and close behavior. It publishes immutable
updates through a callback. A failed refresh retains the last successful
statuses and marks them stale. A successful empty snapshot clears them.
Generation fencing prevents a late read from publishing after close.
