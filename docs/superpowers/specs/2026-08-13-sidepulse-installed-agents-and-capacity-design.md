# SidePulse Installed Agents and Capacity Design

**Date:** 2026-08-13

**Status:** Approved by the user for implementation.

## Outcome

SidePulse becomes a trustworthy manager for interactive coding agents installed
on the Mac, and later for the same agents on another host where SidePulse itself
is installed. It discovers installed coding surfaces without treating presence
as activity, derives lifecycle only from authoritative local evidence, and shows
provider-specific cloud capacity only when the observation can be bound to the
same account and quota pool as an installed surface.

The design has three independent implementation units:

1. [Installed agent registry and Google lifecycle](2026-08-13-sidepulse-installed-agent-registry-design.md)
2. [Provider-specific capacity plane](2026-08-13-sidepulse-provider-capacity-plane-design.md)
3. [Community hardening adaptations](2026-08-13-sidepulse-community-adaptations-design.md)

Each unit produces useful, testable software by itself. They share canonical
`SourceKey`, `WorkKey`, `RequestKey`, `QuotaLaneKey`, freshness, private I/O,
runtime scheduling, and presentation policies. They do not introduce parallel
identity, lifecycle, capacity, timer, worker, persistence, or notification
vocabularies.

## Product Boundary

### Installed agent lifecycle

Lifecycle tracks only interactive coding agents installed on a SidePulse host.
An installation may be a command-line tool, desktop app, IDE extension, or
local harness. Installation, a running process, an open window, a config file,
or a cloud task is not canonical evidence that work is active.

Canonical lifecycle evidence is accepted in this order:

1. an official provider hook with a negotiated capability contract;
2. an official local structured session or transcript source with bounded
   freshness and explicit fallback authority;
3. a supported local provider API that supplies exact session lifecycle;
4. otherwise no lifecycle observation.

Process and window observations may appear in diagnostics as `detected`, but
they cannot create work, requests, completion, failure, or attention facts.

### Provider-specific capacity

Capacity is a separate observation plane. It answers how much remains in an
exact provider, account, pool, model or feature, and semantic window. Cloud
capacity never creates, updates, completes, or fails a local agent session.

SidePulse publishes a numeric capacity lane only when all of these are known:

- the installed surface's provider and authentication mode;
- a stable opaque account or organization discriminator;
- the provider-documented shared pool;
- the exact official observation source;
- the observation time, freshness, and source health;
- a valid `QuotaLaneKey` and reset contract.

If any binding is ambiguous, the UI says `Not Observable`. It does not guess,
merge accounts, infer a percentage from cost, or scrape a web session.

### Host scope

The first implementation is host-local. Identity includes an opaque host and
surface instance discriminator so a future trusted SidePulse peer can publish
the same minimized canonical facts. Cross-host transport, discovery, and
history synchronization are not part of these plans. A future transport must
be explicit, paired, authenticated, bounded, and content-free.

## Shared Identity Model

The existing canonical types remain authoritative:

- `SourceKey` identifies one provider adapter, surface instance, and
  capability;
- `WorkKey`, `RequestKey`, and `SemanticEventKey` identify lifecycle truth;
- `QuotaLaneKey` identifies an exact capacity pool and semantic window;
- `ProviderWatermark` and `SourceFreshness` control ordering and freshness;
- `ObservationAuthority` controls reduction authority.

Installed-surface discovery adds only the inventory key required before a
`SourceKey` exists:

```python
@dataclass(frozen=True, slots=True)
class InstalledSurfaceKey:
    provider_id: str
    surface_id: str
    host_instance_id: str
    surface_instance_id: str
```

`host_instance_id` and `surface_instance_id` are product-generated opaque
identifiers. They never contain a path, user name, account email, bundle path,
workspace name, prompt, repository, executable argument, or credential. When a
surface publishes a capability, its `surface_instance_id` becomes the
`SourceKey.source_instance_id`; SidePulse does not create another runtime key.

Capacity identity adds one validated binding record:

```python
@dataclass(frozen=True, slots=True)
class CapacityAccountBinding:
    provider_id: str
    auth_mode: str
    opaque_account_id: str
    pool_id: str
```

The opaque account identifier is a local HMAC or an official opaque account ID
that has passed the private-data boundary. Display copy uses provider-owned
generic labels such as `Personal subscription`, `Workspace`, or `API
organization`; persisted data never stores email addresses or raw tenant names.

## Supported Surface Policy

A statically reviewed registry declares known surfaces and capabilities. It is
not an arbitrary executable plugin system. Every registration declares:

- provider and product-owned label;
- surface ID and kind;
- exact installation detectors;
- supported capability IDs and schema versions;
- lifecycle evidence class;
- optional hook profile;
- optional capacity profile;
- privacy and permission requirements;
- support state: `full`, `lifecycle`, `capacity`, `inventory`, or `unsupported`.

Discovery never runs an untrusted binary merely because it is found. A binary
may be invoked only through a registration's allowlisted, bounded, read-only
probe, with an exact absolute executable, sanitized environment, timeout,
output-size cap, and product-owned error mapping.

## Initial Surface Set

The registry migrates existing providers without behavior change, then adds the
approved surfaces:

- Codex CLI and Codex desktop;
- Claude Code;
- Devin and Devin Desktop or Windsurf when installed;
- Grok coding surfaces already supported by hooks;
- Cursor CLI and IDE;
- Hermes Agent;
- OpenClaw;
- Antigravity CLI;
- Antigravity desktop and IDE;
- Gemini CLI compatibility and enterprise surface;
- Gemini Code Assist agent mode in VS Code and IntelliJ;
- OpenCode with capacity delegated to its configured model provider;
- GitHub Copilot coding-agent surfaces where a supported local contract exists.

Antigravity CLI and Gemini CLI are the first Google lifecycle adapters because
they document hookable local events. Antigravity desktop, Antigravity IDE, and
Gemini Code Assist remain inventory or capacity-only until an exact installed
version proves that the official hook contract reaches that surface. SidePulse
does not promote them based on branding or shared implementation claims.

## Capacity Pool Policy

Provider pools stay separate unless official documentation proves sharing:

- ChatGPT-plan Codex usage is separate from OpenAI API organization usage;
- Claude consumer and seat-based plans are separate from Console/API billing;
- Google AI plans, Gemini Enterprise, and Google Cloud API billing remain
  distinct unless the installed authentication mode proves the exact pool;
- Cursor team usage, Devin team credits, and GitHub Copilot premium requests
  use their own account and organization scopes;
- OpenCode delegates capacity to the configured provider and publishes no
  independent quota;
- UI-only usage pages without an official automation contract expose an action
  link and `Not Observable`, not scraped numbers.

SidePulse may use an official local status command, official CLI structured
output, official API, or official admin API. It never reads browser cookies,
replays private endpoints, injects JavaScript into provider sites, stores raw
access tokens, or conflates API billing with subscription limits.

## Information Architecture

### Status menu

The menu shows only canonical live, attention, failure, completion, mailbox,
and capacity projections. Installed but idle surfaces do not add rows. Capacity
rows remain grouped by exact provider and account pool, and preserve existing
fresh, stale, partial, error, sign-in-required, and unsupported states.

### Settings

Settings gains one lazy `Installed Agents` pane. It groups surfaces by provider
family and shows product-owned states:

- Installed;
- Configured;
- Monitoring;
- Detection only;
- Sign-in required;
- Capacity available;
- Capacity not observable;
- Migration required;
- Unsupported version.

The pane never exposes raw paths, command lines, tokens, account email, prompt
content, transcript content, or provider exception text. Hook installation and
authorization remain explicit actions. Discovery alone never mutates provider
configuration.

### Diagnostics

The existing operator export boundary gains typed provider-health records.
Diagnostics may include registration ID, support state, capability negotiation,
generic source health, bounded counts, socket reachability, state publication,
and verified device-write health. It excludes raw logs, transcripts, provider
config files, paths, URLs, session labels, account names, emails, and arbitrary
exceptions.

## Scheduling and Resource Budgets

- one immutable registry snapshot per discovery generation;
- at most 64 installed surfaces per host;
- at most 32 provider sources pending in the shared runtime scheduler;
- one latest-wins discovery worker, not one thread per surface;
- capacity refresh remains exact-source keyed with independent deadlines;
- menu open may enqueue stale capacity work but never executes discovery,
  provider I/O, or network work on the AppKit thread;
- failed or hung sources cannot block sibling sources;
- shutdown cancels all generations and joins workers under the existing bounded
  runtime shutdown budget;
- no polling is created for an unsupported or disabled capability.

## Privacy and Security Invariants

- no browser-cookie or private-endpoint ingestion;
- no raw credentials in source, logs, settings, exports, memory caches, or
  exceptions;
- no prompt, message, command, tool argument, workspace path, repository name,
  transcript body, or account email in canonical or capacity persistence;
- installation detectors fail closed on links, parent swaps, ownership or mode
  mismatch, oversized files, unbounded output, and invalid identifiers;
- configuration changes use the existing provider-specific preservation and
  private atomic-I/O boundaries;
- hook payloads are minimized before IPC and cannot gain reduction authority
  through a refresh hint;
- account bindings are exact and cannot merge personal, team, enterprise, API,
  model, or feature pools;
- provider failures expose only product-owned reason codes;
- no new production dependency is added without separate approval.

## Community Adaptation Policy

Community code is evidence and pattern input, not merge authority. Each adopted
idea is reimplemented through SidePulse's canonical interfaces with strict RED
and GREEN evidence. Whole branches and parallel registries are rejected.

Approved adaptations are:

1. relay continuity and real Screen Bar or physical parity;
2. Codex usage-limit terminal recovery with capacity-exhausted cause preserved;
3. Hermes lifecycle and outcome corrections;
4. Cursor payload normalization;
5. privacy-bounded typed diagnostics;
6. Apple Events packaging metadata and Ghostty acceptance where the current
   navigation implementation actually uses Apple Events;
7. transactional isolated installation with rollback and collision refusal;
8. main-session projection and stale-worker retirement;
9. Antigravity and OpenCode only through verified installed contracts.

Default-on audio capture, raw-log ZIP export, quota-as-green-completion,
unverified payload shapes, and whole-fork collector replacements are rejected.

## Acceptance Gates

### Source

- registry tables prove every surface ID, capability, authority, and version is
  unique and deterministic;
- installation never produces lifecycle facts;
- cloud capacity never changes work, request, completion, failure, attention,
  aggregate, notification, or hardware lifecycle state;
- exact account and pool collisions remain isolated;
- unknown or unsupported sources render `Not Observable`;
- lifecycle aliases and terminal causes survive duplicate, out-of-order, stale,
  restart, clock-jump, timeout, and late-generation cases;
- 100 discovery reconciliations and 10,000 same-key updates remain bounded;
- privacy corpus values cannot cross canonical, cache, diagnostic, or export
  boundaries;
- full Ruff, compile, guarded tests, package verification, and diff checks pass.

### Installed runtime

- the signed installed app detects only actually installed surfaces;
- an explicit hook action preserves unrelated provider configuration;
- Antigravity and Gemini lifecycle is observed from real official hooks where
  supported;
- disabled or unsupported surfaces create no timer, worker, network, or hook
  side effect;
- capacity copy matches the exact signed-in pool and refuses mismatches;
- Notification Center posts and click-through navigate to the exact current
  canonical work;
- Screen Bar and physical relay march continuously without phase restart;
- Terminal and Ghostty navigation are observed from the installed signed app;
- sleep, wake, menu tracking, low power, termination, and restart preserve
  resource and generation bounds;
- no test or installed process writes unintentionally to a mounted SidePulse
  device.

## Authority and Delivery Boundaries

Edits and local source verification are approved. No commit, push, pull
request, publication, dependency addition, credential change, silent permission
request, installation, or deployment is implied. Installed permission prompts,
sign-in, provider admin keys, and external account changes require explicit
user-present approval at the moment they are needed.

