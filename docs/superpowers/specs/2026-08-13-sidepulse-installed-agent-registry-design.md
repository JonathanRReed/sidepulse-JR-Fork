# SidePulse Installed Agent Registry and Google Lifecycle Design

**Date:** 2026-08-13

**Status:** Approved component of the SidePulse installed-agents design.

## Goal

Discover interactive coding-agent surfaces installed on the current SidePulse
host, migrate existing providers into one typed inventory, and add Google
Antigravity and Gemini surfaces without treating installation, process
presence, windows, or cloud activity as lifecycle truth.

## Registry Types

The pure registry module defines:

```python
class InstalledSurfaceKind(str, Enum):
    CLI = "cli"
    DESKTOP = "desktop"
    IDE_EXTENSION = "ide_extension"
    LOCAL_HARNESS = "local_harness"


class SurfaceSupportLevel(str, Enum):
    FULL = "full"
    LIFECYCLE = "lifecycle"
    CAPACITY = "capacity"
    INVENTORY = "inventory"
    UNSUPPORTED = "unsupported"


class SurfacePresence(str, Enum):
    ABSENT = "absent"
    INSTALLED = "installed"
    CONFIGURED = "configured"
    MIGRATION_REQUIRED = "migration_required"
    UNSUPPORTED_VERSION = "unsupported_version"


@dataclass(frozen=True, slots=True)
class InstalledSurfaceRegistration:
    provider_id: str
    surface_id: str
    label: str
    kind: InstalledSurfaceKind
    support: SurfaceSupportLevel
    capability_ids: tuple[str, ...]
    hook_profile_id: str | None
    capacity_profile_id: str | None


@dataclass(frozen=True, slots=True)
class InstalledSurfaceObservation:
    key: InstalledSurfaceKey
    presence: SurfacePresence
    version: str | None
    capability_ids: tuple[str, ...]
    reason_code: str | None
```

All identifiers use the existing strict slug or opaque grammar. Labels and
reason codes are product-owned. `version` is bounded and contains only a
validated semantic version or provider-documented short version token.

`InstalledSurfaceObservation` contains no path. Detectors may use a path inside
their private call frame but return only an opaque surface instance derived by
the existing local secret boundary.

## Detection

Each registration owns exact detectors for its surface kind:

- CLI: known application-support locations and a sanitized `PATH` allowlist;
- desktop: known bundle identifiers and exact bundle metadata;
- IDE extension: known user-owned extension roots and extension identifiers;
- local harness: known provider-owned config root and executable identity.

Detection is read-only. It refuses links, path escapes, wrong ownership,
group/world-writable parents, unbounded directories, oversized metadata, and
invalid versions. It does not execute the binary unless the registration
declares a bounded read-only version probe. Version probes use the validated
absolute executable, a sanitized environment, a five-second maximum, a 64 KiB
combined-output cap, and generic failure codes.

## Lifecycle Authority

The registry never emits `ProviderWorkFact`, `ProviderRequestFact`, or
`CanonicalOperatorEvent`. A separate registered adapter may publish those only
after capability negotiation.

Lifecycle sources use existing authority:

- official hook: `AUTHORITATIVE_PROVIDER` or
  `DIRECT_PROVIDER_OBSERVATION`, according to the reviewed contract;
- official local structured fallback: `FALLBACK_OBSERVATION`;
- install, process, window, or config observation: no reducer invocation.

An installed surface with no lifecycle capability appears only in Settings and
diagnostics. It cannot affect menu aggregate, Screen Bar, physical hardware,
completion, interruption, notification, or operator history.

## Google Family

Google is one provider family with distinct surface IDs:

- `antigravity-cli`;
- `antigravity-desktop`;
- `antigravity-ide`;
- `gemini-cli`;
- `gemini-code-assist-vscode`;

Gemini Code Assist for IntelliJ is deferred. Its macOS plugin location is
scoped below a product-and-version directory under `~/Library/Application
Support/JetBrains`; inventory may not enumerate that unbounded directory tree
or infer a versioned path. It will enter the registry only with a separately
reviewed bounded marker.

Antigravity CLI is the primary Google terminal adapter. Gemini CLI remains a
compatibility adapter for supported enterprise, API-key, or installed legacy
use. Both use their official hook event names, official base fields, and exact
installed fixtures. The adapter normalizes content-free identifiers and event
causes before IPC.

The hook vocabulary initially recognizes:

- session start and end;
- before and after agent;
- before and after tool;
- notification;
- before and after model;
- pre-compress;
- before tool selection.

Only events with documented terminal meaning produce terminal facts. A
resource-exhausted or quota-limited result may end a turn, but it carries a
capacity-exhausted cause and never becomes an ordinary green completion.

Antigravity desktop, Antigravity IDE, and Gemini Code Assist agent mode remain
inventory or capacity-only until a real installed version proves that an
official lifecycle hook is delivered for that exact surface. Shared code or a
shared backend is insufficient evidence.

Consumer Gemini Code Assist or Gemini CLI versions that official documentation
marks as retired render `Migration required` with a product-owned link. They do
not silently remap to Antigravity or claim monitoring.

## Existing Provider Migration

Codex, Claude, Devin, Grok, Cursor, Hermes, and OpenClaw registrations migrate
to the inventory without changing their existing hook paths or canonical source
keys. The migration is complete only when before-and-after provider contract,
collector, navigation, mailbox, capacity, and hook-install suites are identical.

OpenCode is registered as a local surface only after its installed configuration
and official event contract are verified. Its model-provider selection does not
change OpenCode lifecycle identity, while capacity remains owned by the selected
provider.

T3 Code is not a provider. Its local harness registry informs the discovery
shape, but SidePulse does not embed its server, WebSocket protocol, cloud
orchestration, or lifecycle vocabulary.

## Settings Projection

The lazy Installed Agents pane groups observations by provider, then surface.
It shows one stable row per `InstalledSurfaceKey` with label, presence, support
level, monitoring state, capacity state, and one explicit configuration action
when supported. Rows update in place and never include raw paths.

The menu does not list installed-only surfaces. A surface enters the mailbox
only through canonical live or recent operator state.

## Tests

- strict missing-module RED for the pure registry;
- deterministic registration table and duplicate rejection;
- absent, installed, configured, migration-required, and unsupported fixtures;
- path-link, ownership, mode, parent-swap, growth, output-size, timeout, and
  invalid-version refusal;
- installation produces zero provider facts, edges, interruptions,
  notifications, or hardware changes;
- Antigravity and Gemini official hook fixture normalization;
- resource exhaustion stays terminal-cause distinct from completion;
- unknown Google payloads fail partial and do not gain authority;
- 64-surface cap, 100 stable reconciliations, 50 lifecycle cycles, restart, and
  late-generation refusal;
- existing provider compatibility gate;
- rendered Settings projection, keyboard, VoiceOver, and 200 percent text;
- independent correctness and privacy reviews before AppKit composition.
