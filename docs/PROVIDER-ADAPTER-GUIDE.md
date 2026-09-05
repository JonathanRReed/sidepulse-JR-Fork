# Provider adapter authoring guide

This guide describes how to add or extend a first-party provider source in JR-Bar. It is intentionally aligned with the current Python contracts, rather than with a hypothetical plugin API. A provider adapter is a bounded translation layer from a provider's native observation surface into content-free SidePulse facts. It is not a plugin loader, credential owner, network client, or mutation API.

## The contract boundary

The source of truth is `src/sidepulse/provider_contracts.py`. Contracts are data-only and are negotiated before any adapter work is accepted. Use the typed values `ProviderIdentifier`, `AdapterIdentifier`, `SourceInstanceIdentifier`, `CapabilityIdentifier`, and `SchemaVersion`. Identifiers are bounded, and a source instance is an opaque routing token, not a credential.

The built-in v1 capability vocabulary is closed in `_CAPABILITY_DEFINITIONS`. Capabilities have an authority: `DISCOVERY`, `OBSERVATION`, or `MUTATION`. The product mapping is a separate allowlist. For example, `live_agent_events` supplies lifecycle, `actionable_requests` supplies questions, and `transcript_usage` or `remote_quota_windows` supplies usage. `account_switching` is mutation-only and is deliberately not mapped to a product capability. Do not make a new product feature by inferring it from an adapter capability.

Construct a declaration with `provider_contract_document(registration)`, then call `negotiate_provider_contract(document)`. A successful read target can be obtained with `contract.action_identity_for("live_agent_events")`, or a product-level target with `contract.product_invocation_for(ProductCapability.LIFECYCLE)`. Both fail closed when the capability is absent, incompatible, mutation-only, or from the wrong source. Unsupported schema major, provider, and adapter values remain visible as bounded identity envelopes, but their capability data is not read.

```python
from sidepulse.provider_contracts import (
    AdapterIdentifier, CapabilityIdentifier, ProviderIdentifier,
    SchemaVersion, SourceInstanceIdentifier,
)
from sidepulse.providers import ProviderSourceRegistration
from sidepulse.provider_facts import ObservationAuthority
from sidepulse.provider_contracts import (
    negotiate_provider_contract, provider_contract_document,
)

registration = ProviderSourceRegistration(
    ProviderIdentifier("codex"),
    AdapterIdentifier("hooks"),
    SourceInstanceIdentifier("global"),
    ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
    ((CapabilityIdentifier("live_agent_events"),
      (SchemaVersion(1, 0), SchemaVersion(1, 1))),),
)
contract = negotiate_provider_contract(provider_contract_document(registration))
target = contract.action_identity_for("live_agent_events")
```

Use a real, reviewed provider ID in examples and code. The registry rejects a provider that is not already reviewed in `_FIRST_PARTY_ADAPTERS`; a new provider therefore requires the corresponding registry, contract, and test changes.

## Provider registry versus adapter registry

`src/sidepulse/providers.py` has two intentionally separate registries.

`PROVIDER_SPECS` and `PROVIDER_REGISTRY` describe native hook integration: display label, native event names, config kind, config path, and the detector. `provider_spec("codex")`, `detect_provider_configs()`, `detect_log_path()`, `canonical_event_name()`, and `normalize_event_payload()` are the integration-facing APIs. The current provider list is Codex, Claude, Devin, Grok, Cursor, Hermes, OpenClaw, OpenCode, Antigravity, and Kiro. Preserve each provider's native config shape. Antigravity grouped events, OpenClaw handlers, OpenCode plugins, and Kiro's managed agent file are not interchangeable JSON hook files.

`ProviderSourceRegistration` and `_PROVIDER_SOURCE_REGISTRATIONS` describe canonical read sources. One provider can have multiple sources and instances. Current examples include Codex hooks/global, Codex transcripts/local, Codex quota/local, Claude hooks/global, Claude transcripts/local, and Claude quota/oauth. The `SourceKey(provider_id, adapter_id, source_instance_id, capability_id)` is the identity carried through facts. Never collapse `claude/quota/oauth` into `claude/quota/local`, because the instance keeps retry and account state separate.

`negotiated_provider_sources()` is the canonical enumeration. It emits one `NegotiatedProviderSource` row per declared read capability, with the maximum declared version and the negotiated capability, if any. `observation_invocation_allowed` is the read gate. It requires a supported or partial contract, a discovery or observation authority, and a negotiated capability.

## Adapter normalization

The implementation boundary is `src/sidepulse/provider_adapters.py`.

1. Accept the provider's native event only at the outer boundary as `HookEvent`.
2. Call `minimize_hook_event(record, source_key=..., contract=..., observation_authority=...)`.
3. Copy only typed, allowlisted scalars into `NormalizedProviderRecord`. The result includes `event_name`, time, event token, work/request identity, parent identity, safe label, notification kind, sequence, and terminal cause. It does not retain prompt text, transcript text, paths, URLs, or credentials.
4. Unknown, malformed, mismatched, or unsupported input becomes an `InertProviderRecord` with a bounded diagnostic such as `unknown_provider_event`, `invalid_provider_identity`, or `contract_not_observable`.
5. Pass either record to `provider_facts_for_record(..., observed_at_epoch=...)` to produce a source-scoped `ProviderFactBatch`.

The normalizer uses `_PROVIDER_EVENT_RULES` and provider-specific refinements for Cursor, Hermes, and Antigravity. Add native aliases in `providers.py` and event rules in `provider_adapters.py`; do not teach downstream reducers about provider-specific event spelling. Event tokens are preserved when valid, otherwise deterministically derived. Work and request IDs are bounded opaque identities. A provider's notification must map to one of the closed `NotificationKind` values.

```python
normalized = minimize_hook_event(
    hook_event,
    source_key=SourceKey("claude", "hooks", "global", "live_agent_events"),
    contract=contract,
    observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
)
facts = provider_facts_for_record(
    normalized,
    contract=contract,
    observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
    observed_at_epoch=now,
)
```

`provider_facts_for_record` enforces the source and contract match. Request facts additionally require `actionable_requests` and direct provider observation. A transcript fallback must never manufacture an actionable request. Preserve partial health and diagnostics rather than replacing unknown data with an apparently idle state.

## Instances and settings ownership

`src/sidepulse/provider_instances.py` owns non-secret instance identity and durable profile choices. `ProviderInstanceKey(provider_id, source_instance_id)` is safe for dictionary keys and diagnostics. `ProviderInstanceProfile` stores a bounded label, optional color, retention of `0`, `7`, `30`, or `90` days, `never` or `status_only` remote sharing, an `app`, `terminal`, or `vscode` open action, and opaque consent or credential-account references. It must never contain secret values.

`src/sidepulse/provider_feature_settings.py` gives each consumer a narrow immutable view. Collectors consume `ProviderCollectionSettings`; menus consume `ProviderPresentationSettings`; instance identity, retention, sharing, and session actions use their respective policy projections; sync consumes `ProviderSyncSettingsProjection`. Use `project_provider_feature_settings()` and the projection types instead of passing the durable settings document into a collector or UI. A provider feature must identify the exact `(provider_id, source_instance_id)` pair, and duplicate identities are rejected.

## Fixture ownership and integration compatibility

Synthetic fixtures live under `tests/fixtures/` and are owned by `src/sidepulse/resources/provider_fixture_ownership.json`. `validate_provider_fixture_ownership()` requires exactly one manifest entry for every provider in `PROVIDER_REGISTRY`, a bounded JSON file, a SHA-256 receipt, a review date, and `synthetic: true`. The validator rejects paths, prompts, transcripts, emails, token-shaped values, and unowned files. Add a fixture by updating the ownership manifest and its test, never by copying production traffic.

External integrations have a different boundary. `src/sidepulse/integration_compatibility.py` loads the packaged `integration_compatibility.json`, and `docs/INTEGRATIONS.md` documents the current T3 Code read-only integration. Compatibility records must identify the reviewed upstream commit, protocol fingerprint, version window, fixture version, and connection mode. An integration adapter may read an approved projection, but it must not be silently promoted to a native provider source or gain mutation authority. If a future `docs/provider/integrations/` guide exists, keep it subordinate to the packaged manifest and current integration docs.

## Safe extension workflow

1. Inventory the native provider contract and choose the smallest source and capability. Record whether evidence is direct observation, discovery, or fallback.
2. Add a `ProviderSpec` with the provider's real config path and native event names. In the generic detector, an unreadable existing config degrades to `exists=True, hooks_enabled=False`. OpenCode is an intentional exception: its detector accepts only a readable, regular, non-symlink SidePulse-managed plugin within the private read limit; a missing or unreadable plugin is reported as not installed (`exists=False, hooks_enabled=False`) so an unverified plugin is never treated as active.
3. Add a reviewed `ProviderSourceRegistration` using typed identifiers and only capabilities whose semantics are real. Add the provider to `_FIRST_PARTY_ADAPTERS` and any product mapping only when the product behavior is supported.
4. Add event aliases and `_PROVIDER_EVENT_RULES`. Normalize to facts through the two adapter functions. Keep the adapter pure after ingress.
5. Add synthetic, owned fixtures and negative cases for identity mismatch, unknown event, missing IDs, unsupported capability, malformed time, and credential-like data.
6. Add instance and feature-setting projections when multiple accounts or source instances can coexist. Keep credential and consent references opaque.
7. Update the integration compatibility record and docs only for an independently reviewed external projection.
8. Run the targeted provider contract, adapter, fixture, settings, and integration tests, then the repository's prescribed lint/type checks. Verify source-level output and the installed UI separately. This guide intentionally does not claim tests were run as part of this documentation change.

## Verification map

The primary tests are `tests/test_provider_contracts.py`, `tests/test_provider_instances.py`, `tests/test_provider_feature_settings.py`, `tests/test_provider_fixture_ownership.py`, `tests/test_provider_usage_collectors.py`, `tests/test_provider_usage_runtime.py`, `tests/test_provider_usage_sync.py`, `tests/test_provider_browser_import.py`, `tests/test_provider_reconnect.py`, and `tests/test_integration_cli_entrypoint.py`. Also inspect provider-specific wiring tests and `tests/fixtures/`.

Acceptance means more than a green unit test: the exact provider and source identity must negotiate, the detector must recognize its installed configuration, native events must minimize into content-free records, downstream facts must preserve freshness and authority, settings must address instances independently, and the live UI must show truthful stale, unavailable, and unsupported states.
