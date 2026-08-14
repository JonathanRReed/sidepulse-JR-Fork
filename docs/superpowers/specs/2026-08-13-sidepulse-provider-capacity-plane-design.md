# SidePulse Provider Capacity Plane Design

**Date:** 2026-08-13

**Status:** Approved component of the SidePulse installed-agents design.

## Goal

Observe optional cloud or account capacity for an installed coding surface only
when SidePulse can prove the matching provider, authentication mode, account,
pool, and semantic quota window. Preserve exact uncertainty and never create
lifecycle truth from usage.

## Source Classes

```python
class CapacityEvidenceClass(str, Enum):
    OFFICIAL_LOCAL = "official_local"
    OFFICIAL_API = "official_api"
    OFFICIAL_ADMIN_API = "official_admin_api"
    UI_LINK_ONLY = "ui_link_only"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class AccountCapacitySourceRegistration:
    provider_id: str
    capacity_profile_id: str
    evidence_class: CapacityEvidenceClass
    auth_modes: tuple[str, ...]
    capability_id: str
    pool_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapacityAccountBinding:
    provider_id: str
    auth_mode: str
    opaque_account_id: str
    pool_id: str
```

Registrations are static and reviewed. Bindings are runtime observations that
contain no secret or display identity. A source cannot publish until its binding
matches the installed surface and the observed quota response.

The existing `SourceKey`, `QuotaLaneKey`, `CapacityObservation`, source-health,
refresh coordinator, history, reset, authority, view, and forecast types remain
the capacity model. This design does not add a second quota or percentage type.

## Provider Matrix

### OpenAI

- ChatGPT-authenticated Codex uses a ChatGPT-plan capacity profile.
- API-key-authenticated Codex uses a separate API-organization profile.
- Codex access-token automation uses its exact ChatGPT workspace identity but
  never exposes or persists the token.
- cloud tasks do not create local lifecycle truth.
- ChatGPT-plan and API-organization lanes never merge.
- an official local status source may publish remaining plan limits when its
  structured contract is stable; the OpenAI API Usage API publishes only API
  organization usage and cost.

### Anthropic

- Claude consumer subscription use is shared across Claude and Claude Code.
- Team and seat-based Enterprise use exact organization capacity.
- Console/API billing is a separate organization pool.
- `/usage` or another official local structured surface may publish plan usage
  only when it has an automation-safe contract.
- the Usage and Cost Admin API and Claude Code Analytics API require explicit
  organization-admin configuration and never run by default.

### Google

- Gemini CLI and Gemini Code Assist agent mode share a pool only for the exact
  documented account or edition.
- Antigravity profiles remain separate when the Google AI plan or enterprise
  binding differs.
- Google Cloud API quotas are not a substitute for a Google AI subscription.
- official CLI quota or credit output may publish local capacity when structured
  and account-bound; otherwise Settings links to the official view and shows
  `Not Observable`.

### Other providers

- GitHub Copilot premium requests use exact user or organization license scope;
  official UI-only data remains link-only until a public automation contract is
  available.
- Cursor team or organization APIs are opt-in and account exact.
- Devin or Windsurf team credit APIs are opt-in and account exact.
- OpenCode publishes no independent capacity. The configured model provider
  owns the lane.
- T3 Code and T3 Chat are not provider-capacity sources for installed coding
  agents without an official exact binding.

## Observation Rules

Every observation must prove:

- exact `CapacityAccountBinding`;
- exact `SourceKey` negotiated for capacity observation;
- exact pool, feature or model scope, window, and effect in `QuotaLaneKey`;
- observed value or explicit null state;
- reset state and time when available;
- source health and collection time;
- the provider's documented relation between used and remaining values.

Percent values are never derived from spend unless the provider defines spend
as the quota. Cost, request count, credits, tokens, premium requests, and
percentage are distinct units. The current SidePulse presentation supports
percentage remaining; sources with other units remain detail-only until the
capacity domain adds an explicit reviewed unit.

An explicit empty success clears only the exact source and account lanes. A
failure retains only matching last-known-good data. Account, auth-mode, pool,
edition, model, or feature changes invalidate the old generation, timers,
history binding, forecast, and reset plan.

## Refresh and Credentials

- capacity is disabled until its installed surface and source are discovered;
- local sources use the existing exact-source refresh coordinator;
- network and admin sources are separately opt-in;
- credentials use the provider's supported keychain or environment mechanism;
- SidePulse settings persist only a credential reference and opaque account
  binding, never the secret;
- no browser cookies, web storage, private endpoints, DOM scraping, or session
  replay;
- menu open enqueues stale work but performs no network call on the AppKit
  thread;
- one source timeout cannot block or erase sibling sources;
- cooldown, retry, outer deadline, queued manual refresh, and late-generation
  behavior reuse current capacity refresh policy;
- raw provider errors become typed product-owned health codes.

## UI

The Capacity card groups exact account and pool lanes. Generic account labels
are stable and locally assigned. A detail row shows source type, freshness,
health, last success, reset, and whether the number is local, API, admin, or
unobservable.

`Not Observable` is a first-class state. It may include an explicit `View usage`
action to the provider's official page, but the page is never scraped. `Sign in
required`, `Access denied`, `Unsupported`, `Partial`, `Timed out`, `Stale`, and
`Last known` remain distinct.

Cloud task presence may be shown in a future separate remote-task surface. It
does not appear as local mailbox activity, attention, completion, history, or
hardware state in this plan.

## Tests

- registration and binding validation for every provider profile;
- ChatGPT versus API-org, Claude subscription versus Console, and Google plan
  versus Cloud API collision refusal;
- account-switch invalidation and zero cross-account last-known reuse;
- official local, official API, admin API, UI-only, and unsupported source
  behavior;
- no cookie, private-endpoint, email, token, path, raw response, or exception
  in settings, cache, history, diagnostics, repr, or export;
- capacity observations cannot change operator state, mailbox, interruption,
  completion, notification, relay, or hardware output;
- explicit empty, partial, timeout, malformed, slow, late, stale, reset, and
  clock-jump stories;
- no network source starts when disabled, signed out, or menu-irrelevant;
- independent sibling deadlines and one-deep manual cooldown queue;
- 32-lane and 2 MiB persistence caps remain enforced;
- rendered source-health, Not Observable, and accessibility copy;
- independent correctness, privacy, and credential-boundary reviews.

