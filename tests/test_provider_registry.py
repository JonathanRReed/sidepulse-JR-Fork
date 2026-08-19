from __future__ import annotations

from sidepulse import providers
from sidepulse.capacity_types import SourceKey
from sidepulse.provider_contracts import (
    AdapterIdentifier,
    CapabilityIdentifier,
    ContractStatus,
    ProviderIdentifier,
    SchemaVersion,
    SourceInstanceIdentifier,
)
from sidepulse.provider_facts import ObservationAuthority
from sidepulse.providers import (
    HOOK_PROVIDERS,
    NegotiatedProviderSource,
    ProviderSourceRegistration,
    negotiated_provider_sources,
    provider_capacity_source_registrations,
    provider_source_registrations,
    sources_with_capability,
)


def _row_identity(row: NegotiatedProviderSource) -> tuple[str, str, str, str]:
    return (
        row.source_key.provider_id,
        row.source_key.adapter_id,
        row.source_key.source_instance_id,
        row.source_key.capability_id,
    )


def test_static_registry_has_literal_deterministic_source_and_capability_order() -> None:
    """Deriving order from a set or detector result would make source scheduling drift."""
    registrations = provider_source_registrations()

    assert type(registrations) is tuple
    assert tuple(
        (
            registration.provider_id.value,
            registration.adapter_id.value,
            registration.source_instance_id.value,
            registration.observation_authority,
            tuple(capability.value for capability, _versions in registration.capability_versions),
        )
        for registration in registrations
    ) == (
        (
            "codex",
            "hooks",
            "global",
            ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
            ("live_agent_events", "actionable_requests"),
        ),
        (
            "codex",
            "transcripts",
            "local",
            ObservationAuthority.FALLBACK_OBSERVATION,
            ("transcript_usage",),
        ),
        (
            "codex",
            "quota",
            "local",
            ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
            ("remote_quota_windows",),
        ),
        (
            "claude",
            "hooks",
            "global",
            ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
            ("live_agent_events", "actionable_requests"),
        ),
        (
            "claude",
            "transcripts",
            "local",
            ObservationAuthority.FALLBACK_OBSERVATION,
            ("transcript_usage",),
        ),
        (
            "claude",
            "quota",
            "oauth",
            ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
            ("remote_quota_windows",),
        ),
        (
            "devin",
            "hooks",
            "global",
            ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
            ("live_agent_events", "actionable_requests"),
        ),
        (
            "grok",
            "hooks",
            "global",
            ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
            ("live_agent_events", "actionable_requests"),
        ),
        (
            "cursor",
            "hooks",
            "global",
            ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
            ("live_agent_events",),
        ),
        (
            "hermes",
            "hooks",
            "global",
            ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
            ("live_agent_events",),
        ),
        (
            "openclaw",
            "hooks",
            "global",
            ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
            ("live_agent_events",),
        ),
        (
            "opencode",
            "hooks",
            "global",
            ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
            ("live_agent_events", "actionable_requests"),
        ),
        # live_agent_events only: SidePulse never registers Antigravity's
        # PreToolUse hook, so no Antigravity event can name a live request.
        (
            "antigravity",
            "hooks",
            "global",
            ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
            ("live_agent_events",),
        ),
        (
            "kiro",
            "hooks",
            "global",
            ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
            ("live_agent_events",),
        ),
    )


def test_each_hook_provider_has_exactly_one_registered_hook_source() -> None:
    """Omitting or duplicating a hook source would drop or double-schedule a provider."""
    hook_registrations = tuple(
        registration
        for registration in provider_source_registrations()
        if registration.adapter_id == AdapterIdentifier("hooks")
    )

    assert tuple(registration.provider_id.value for registration in hook_registrations) == (
        "codex",
        "claude",
        "devin",
        "grok",
        "cursor",
        "hermes",
        "openclaw",
        "opencode",
        "antigravity",
        "kiro",
    )
    assert tuple(registration.provider_id.value for registration in hook_registrations) == (
        HOOK_PROVIDERS
    )


def test_negotiated_rows_use_unique_canonical_source_keys_one_per_capability() -> None:
    """Dropping capability identity would collapse sibling facts from one source instance."""
    rows = negotiated_provider_sources()
    keys = tuple(row.source_key for row in rows)

    assert all(type(key) is SourceKey for key in keys)
    assert len(keys) == len(set(keys)) == 19
    assert tuple(_row_identity(row) for row in rows[:4]) == (
        ("codex", "hooks", "global", "live_agent_events"),
        ("codex", "hooks", "global", "actionable_requests"),
        ("codex", "transcripts", "local", "transcript_usage"),
        ("codex", "quota", "local", "remote_quota_windows"),
    )
    assert all(
        row.source_key
        == SourceKey(
            row.registration.provider_id.value,
            row.registration.adapter_id.value,
            row.registration.source_instance_id.value,
            row.declared_capability_id.value,
        )
        for row in rows
    )


def test_capacity_source_registrations_are_exact_and_separate_from_hook_ownership() -> None:
    """A provider family fallback would collapse account pools or create lifecycle truth."""
    registrations = provider_capacity_source_registrations()

    assert tuple(
        (
            registration.provider_id.value,
            registration.adapter_id.value,
            registration.source_instance_id.value,
        )
        for registration in registrations
    ) == (
        ("codex", "quota", "local"),
        # Claude's is a separate source instance, not a second row on the
        # transcripts one: a shared instance would share retry state, and an
        # OAuth 401 loop would take local transcript usage down with it.
        ("claude", "quota", "oauth"),
    )
    assert all(
        tuple(capability.value for capability, _versions in registration.capability_versions)
        == ("remote_quota_windows",)
        for registration in registrations
    )
    assert not any(registration.provider_id.value == "opencode" for registration in registrations)


def test_known_pairs_negotiate_exact_versions_and_absent_capabilities_stay_absent() -> None:
    """Inferring related capabilities would fabricate provider observation authority."""
    rows = negotiated_provider_sources()
    codex_hooks = tuple(
        row
        for row in rows
        if row.source_key.provider_id == "codex" and row.source_key.adapter_id == "hooks"
    )
    openclaw_hooks = tuple(
        row
        for row in rows
        if row.source_key.provider_id == "openclaw"
    )

    assert tuple(
        (row.declared_capability_id.value, row.negotiated_capability.version)
        for row in codex_hooks
        if row.negotiated_capability is not None
    ) == (
        ("live_agent_events", SchemaVersion(1, 1)),
        ("actionable_requests", SchemaVersion(1, 0)),
    )
    assert tuple(row.declared_capability_id.value for row in openclaw_hooks) == (
        "live_agent_events",
    )
    opencode_hooks = tuple(
        row
        for row in rows
        if row.source_key.provider_id == "opencode"
    )
    assert tuple(
        (row.source_key, row.declared_capability_id.value)
        for row in opencode_hooks
    ) == (
        (
            SourceKey("opencode", "hooks", "global", "live_agent_events"),
            "live_agent_events",
        ),
        (
            SourceKey("opencode", "hooks", "global", "actionable_requests"),
            "actionable_requests",
        ),
    )
    assert all(row.contract.status is ContractStatus.SUPPORTED for row in rows)
    assert not any(
        row.declared_capability_id.value
        in {
            "direct_navigation",
            "reset_metadata",
            "account_identity",
            "history_attribution",
            "account_switching",
        }
        for row in rows
    )


def test_capability_filter_returns_only_negotiated_invocation_eligible_rows() -> None:
    """Filtering declarations alone would schedule unsupported or mutation-only work."""
    rows = negotiated_provider_sources()

    assert tuple(
        _row_identity(row)
        for row in sources_with_capability(rows, CapabilityIdentifier("transcript_usage"))
    ) == (
        ("codex", "transcripts", "local", "transcript_usage"),
        ("claude", "transcripts", "local", "transcript_usage"),
    )
    assert sources_with_capability(
        rows,
        CapabilityIdentifier("account_switching"),
    ) == ()


def test_unsupported_capability_major_remains_visible_but_ineligible(monkeypatch) -> None:
    """Discarding an incompatible source would hide health, while scheduling it is unsafe."""
    unsupported = ProviderSourceRegistration(
        provider_id=ProviderIdentifier("codex"),
        adapter_id=AdapterIdentifier("hooks"),
        source_instance_id=SourceInstanceIdentifier("future"),
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        capability_versions=(
            (
                CapabilityIdentifier("live_agent_events"),
                (SchemaVersion(2, 0),),
            ),
        ),
    )
    monkeypatch.setattr(providers, "_PROVIDER_SOURCE_REGISTRATIONS", (unsupported,))

    rows = negotiated_provider_sources()

    assert len(rows) == 1
    assert rows[0].source_key == SourceKey(
        "codex",
        "hooks",
        "future",
        "live_agent_events",
    )
    assert rows[0].contract.status is ContractStatus.PARTIAL
    assert rows[0].negotiated_capability is None
    assert rows[0].observation_invocation_allowed is False
    assert sources_with_capability(
        rows,
        CapabilityIdentifier("live_agent_events"),
    ) == ()


def test_unknown_source_stays_visible_with_zero_invocation_eligibility(monkeypatch) -> None:
    """A valid-looking unknown provider must never become an implicit plugin source."""
    unknown = ProviderSourceRegistration(
        provider_id=ProviderIdentifier("future-provider"),
        adapter_id=AdapterIdentifier("hooks"),
        source_instance_id=SourceInstanceIdentifier("global"),
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        capability_versions=(
            (
                CapabilityIdentifier("live_agent_events"),
                (SchemaVersion(1, 1),),
            ),
        ),
    )
    monkeypatch.setattr(providers, "_PROVIDER_SOURCE_REGISTRATIONS", (unknown,))

    rows = negotiated_provider_sources()

    assert len(rows) == 1
    assert rows[0].contract.status is ContractStatus.UNSUPPORTED_PROVIDER
    assert rows[0].negotiated_capability is None
    assert rows[0].observation_invocation_allowed is False
    assert sources_with_capability(
        rows,
        CapabilityIdentifier("live_agent_events"),
    ) == ()
