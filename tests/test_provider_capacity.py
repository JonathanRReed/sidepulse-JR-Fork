from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from sidepulse.capacity_types import CapacityEvidenceClass, QuotaEffect, SourceKey
from sidepulse.provider_capacity import (
    AccountCapacitySourceRegistration,
    CapacityPolicyState,
    ProviderCapacityPolicy,
    ProviderCapacityPolicyError,
    negotiate_provider_capacity_policies,
    provider_capacity_policies,
    select_opencode_capacity_policy,
    select_provider_capacity_policy,
)
from sidepulse.provider_contracts import (
    MAX_CAPABILITY_DECLARATIONS,
    ContractValidationError,
    negotiate_capacity_source_contract,
)
from sidepulse.providers import negotiated_provider_sources


def _summary(policy: ProviderCapacityPolicy) -> tuple[object, ...]:
    return (
        policy.profile_id,
        policy.provider_id,
        policy.evidence_class,
        policy.auth_modes,
        policy.pool_ids,
        policy.opt_in_required,
        policy.state,
        policy.source,
    )


def test_literal_policy_table_keeps_exact_provider_account_pools_separate() -> None:
    """Collapsing plan, organization, edition, or billing pools would misstate capacity."""
    policies = provider_capacity_policies()

    assert type(policies) is tuple
    assert all(isinstance(policy, AccountCapacitySourceRegistration) for policy in policies)
    assert policies[0].capacity_profile_id == "openai-codex-consumer"
    assert policies[0].capability_id == "remote_quota_windows"
    assert tuple(_summary(policy) for policy in policies) == (
        (
            "openai-codex-consumer",
            "codex",
            CapacityEvidenceClass.OFFICIAL_LOCAL,
            ("chatgpt", "chatgpt-access-token"),
            ("codex-chatgpt-plan",),
            False,
            CapacityPolicyState.OBSERVABLE,
            SourceKey("codex", "quota", "local", "remote_quota_windows"),
        ),
        (
            "openai-api-organization",
            "openai",
            CapacityEvidenceClass.OFFICIAL_API,
            ("api-key", "service-account"),
            ("openai-api-organization",),
            True,
            CapacityPolicyState.DETAIL_ONLY,
            SourceKey("openai", "usage-api", "organization", "remote_quota_windows"),
        ),
        (
            "anthropic-consumer",
            "claude",
            CapacityEvidenceClass.OFFICIAL_API,
            ("consumer",),
            ("claude-consumer-plan",),
            True,
            CapacityPolicyState.OBSERVABLE,
            SourceKey("claude", "quota", "oauth", "remote_quota_windows"),
        ),
        (
            "anthropic-team-enterprise",
            "claude",
            CapacityEvidenceClass.OFFICIAL_ADMIN_API,
            ("team", "enterprise"),
            ("claude-team-seat", "claude-enterprise-seat"),
            True,
            CapacityPolicyState.DETAIL_ONLY,
            SourceKey("claude", "analytics-api", "organization", "remote_quota_windows"),
        ),
        (
            "anthropic-console-api",
            "anthropic",
            CapacityEvidenceClass.OFFICIAL_ADMIN_API,
            ("console-api-key", "console-oauth"),
            ("anthropic-console-organization",),
            True,
            CapacityPolicyState.DETAIL_ONLY,
            SourceKey("anthropic", "usage-api", "organization", "remote_quota_windows"),
        ),
        (
            "google-gemini-code-assist",
            "google",
            CapacityEvidenceClass.OFFICIAL_LOCAL,
            ("google-ai", "code-assist", "gemini-enterprise"),
            ("google-ai-plan", "gemini-enterprise"),
            False,
            CapacityPolicyState.OBSERVABLE,
            SourceKey("google", "quota", "gemini-plan", "remote_quota_windows"),
        ),
        (
            "google-antigravity",
            "google",
            CapacityEvidenceClass.UI_LINK_ONLY,
            ("antigravity-plan", "antigravity-enterprise"),
            ("google-antigravity-plan", "google-antigravity-enterprise"),
            False,
            CapacityPolicyState.LINK_ONLY,
            None,
        ),
        (
            "google-cloud-api",
            "google",
            CapacityEvidenceClass.OFFICIAL_API,
            ("google-cloud-api-key", "google-cloud-oauth", "service-account"),
            ("google-cloud-project",),
            True,
            CapacityPolicyState.DETAIL_ONLY,
            SourceKey("google", "quota", "cloud-api", "remote_quota_windows"),
        ),
        (
            "github-copilot",
            "github",
            CapacityEvidenceClass.UI_LINK_ONLY,
            ("copilot-user", "copilot-organization"),
            ("github-copilot-user", "github-copilot-organization"),
            False,
            CapacityPolicyState.LINK_ONLY,
            None,
        ),
        (
            "cursor-team-organization",
            "cursor",
            CapacityEvidenceClass.OFFICIAL_API,
            ("team-api", "organization-api"),
            ("cursor-team", "cursor-organization"),
            True,
            CapacityPolicyState.DETAIL_ONLY,
            SourceKey("cursor", "account-api", "organization", "remote_quota_windows"),
        ),
        (
            "devin-windsurf-team",
            "devin",
            CapacityEvidenceClass.OFFICIAL_API,
            ("devin-team-api", "windsurf-team-api"),
            ("devin-team", "windsurf-team"),
            True,
            CapacityPolicyState.DETAIL_ONLY,
            SourceKey("devin", "account-api", "team", "remote_quota_windows"),
        ),
        (
            "opencode-upstream-delegation",
            "opencode",
            CapacityEvidenceClass.UNSUPPORTED,
            ("upstream-config",),
            (),
            False,
            CapacityPolicyState.UPSTREAM_DELEGATED,
            None,
        ),
    )
    assert len({policy.profile_id for policy in policies}) == len(policies)


def test_semantic_lanes_are_literal_bounded_and_immutable() -> None:
    """Provider labels or runtime reset durations must not invent lane semantics."""
    policies = provider_capacity_policies()
    codex = policies[0]
    claude = policies[2]

    def identities(policy):
        return tuple(
            (
                lane.pool_id,
                lane.opaque_scope,
                lane.model,
                lane.window,
                lane.effect,
                lane.semantic_name,
                lane.bindable,
            )
            for lane in policy.lanes
        )

    assert identities(codex) == (
        (
            "codex-chatgpt-plan",
            "all",
            None,
            "five-hour",
            QuotaEffect.ALL_WORKLOADS,
            "5-hour window",
            True,
        ),
        (
            "codex-chatgpt-plan",
            "all",
            None,
            "weekly",
            QuotaEffect.ALL_WORKLOADS,
            "Weekly window",
            True,
        ),
    )
    # The two weekly sub-caps share a pool and a window with the weekly
    # ceiling and differ only by model. Folding them together, or deriving
    # them from whatever `claude_quota.fetch_windows` happened to return,
    # would give the authority layer nothing to tell apart -- and an Opus
    # ceiling would then speak for a Sonnet session.
    assert identities(claude) == (
        (
            "claude-consumer-plan",
            "all",
            None,
            "five-hour",
            QuotaEffect.ALL_WORKLOADS,
            "5-hour",
            True,
        ),
        (
            "claude-consumer-plan",
            "all",
            None,
            "weekly",
            QuotaEffect.ALL_WORKLOADS,
            "Weekly",
            True,
        ),
        (
            "claude-consumer-plan",
            "all",
            "opus",
            "weekly",
            QuotaEffect.MODEL,
            "Weekly Opus",
            True,
        ),
        (
            "claude-consumer-plan",
            "all",
            "sonnet",
            "weekly",
            QuotaEffect.MODEL,
            "Weekly Sonnet",
            True,
        ),
    )
    assert all(type(policy.auth_modes) is tuple for policy in policies)
    assert all(type(policy.lanes) is tuple for policy in policies)
    with pytest.raises(FrozenInstanceError):
        codex.provider_id = "openai"  # type: ignore[misc]


def test_exact_provider_and_auth_mode_select_one_policy_without_pool_inheritance() -> None:
    """A family label alone must not merge consumer, admin, or cloud billing."""
    codex = select_provider_capacity_policy("codex", "chatgpt")
    openai = select_provider_capacity_policy("openai", "api-key")
    consumer = select_provider_capacity_policy("claude", "consumer")
    team = select_provider_capacity_policy("claude", "team")
    console = select_provider_capacity_policy("anthropic", "console-api-key")
    google_plan = select_provider_capacity_policy("google", "google-ai")
    google_cloud = select_provider_capacity_policy("google", "google-cloud-oauth")

    assert codex is not None and codex.pool_ids == ("codex-chatgpt-plan",)
    assert openai is not None and openai.pool_ids == ("openai-api-organization",)
    assert consumer is not None and consumer.pool_ids == ("claude-consumer-plan",)
    assert consumer.state is CapacityPolicyState.OBSERVABLE
    # The consumer subscription pool and the seat pools stay separate even
    # though both answer to "claude": a Max plan must never inherit Team seat
    # capacity, or the reverse.
    assert consumer.pool_ids != team.pool_ids
    assert team is not None and team.pool_ids == (
        "claude-team-seat",
        "claude-enterprise-seat",
    )
    assert console is not None and console.pool_ids == (
        "anthropic-console-organization",
    )
    assert google_plan is not None and google_plan.profile_id == (
        "google-gemini-code-assist"
    )
    assert google_cloud is not None and google_cloud.profile_id == "google-cloud-api"
    assert select_provider_capacity_policy("claude", "console-api-key") is None
    assert select_provider_capacity_policy("google", "api-key") is None


def test_each_actual_policy_source_negotiates_exactly_one_registered_capability() -> None:
    """Missing or duplicate source rows must fail instead of inheriting a provider source."""
    available = negotiated_provider_sources()
    negotiated = negotiate_provider_capacity_policies(available)

    actual = tuple(row for row in negotiated if row.negotiated_source is not None)
    assert tuple(row.policy.profile_id for row in actual) == (
        "openai-codex-consumer",
        "anthropic-consumer",
    )
    assert all(row.negotiated_source is not None for row in actual)
    assert all(row.descriptor is not None for row in actual)
    assert all(
        row.negotiated_source is not None
        and row.negotiated_source.source_key == row.policy.source
        and row.negotiated_source.source_key.provider_id == row.policy.provider_id
        for row in actual
    )
    assert all(
        row.negotiated_source is None and row.descriptor is None
        for row in negotiated
        if row not in actual
    )

    codex_source = next(
        source
        for source in available
        if source.source_key
        == SourceKey("codex", "quota", "local", "remote_quota_windows")
    )
    with pytest.raises(ProviderCapacityPolicyError, match="exactly one negotiated source"):
        negotiate_provider_capacity_policies((*available, codex_source))
    without_codex = tuple(source for source in available if source is not codex_source)
    inactive = negotiate_provider_capacity_policies(without_codex)
    assert next(
        row for row in inactive if row.policy.profile_id == "openai-codex-consumer"
    ).negotiated_source is None


def test_policy_construction_rejects_implicit_provider_inheritance() -> None:
    """OpenCode or another surface cannot stamp its provider id onto an upstream source."""
    upstream = provider_capacity_policies()[1]

    with pytest.raises(ProviderCapacityPolicyError, match="source provider mismatch"):
        replace(upstream, provider_id="opencode")


class _PoisonSource:
    def __getattr__(self, _name: str) -> object:
        raise AssertionError("unvalidated capacity source was inspected")


def test_capacity_source_negotiation_rejects_unbounded_or_custom_sources_first() -> None:
    """Unbounded or provider-shaped objects must not be traversed during pure negotiation."""
    available = negotiated_provider_sources()
    declared = SourceKey("codex", "quota", "local", "remote_quota_windows")
    codex_source = next(source for source in available if source.source_key == declared)

    with pytest.raises(ContractValidationError, match="too many capacity sources"):
        negotiate_capacity_source_contract(
            declared,
            (codex_source,) * (MAX_CAPABILITY_DECLARATIONS + 1),
        )
    with pytest.raises(ProviderCapacityPolicyError, match="invalid available capacity sources"):
        negotiate_provider_capacity_policies((*available, _PoisonSource()))


@pytest.mark.parametrize(
    ("configured_model", "profile_id", "provider_id"),
    [
        ("openai/gpt-5", "openai-api-organization", "openai"),
        ("anthropic/claude-sonnet-4", "anthropic-console-api", "anthropic"),
        ("google/gemini-2.5-pro", "google-cloud-api", "google"),
        ("github-copilot/gpt-4.1", "github-copilot", "github"),
    ],
)
def test_opencode_selects_only_the_exact_bounded_upstream_prefix(
    configured_model: str,
    profile_id: str,
    provider_id: str,
) -> None:
    """Configured upstream ownership must never become OpenCode-owned capacity."""
    policy = select_opencode_capacity_policy(configured_model)

    assert policy is not None
    assert policy.profile_id == profile_id
    assert policy.provider_id == provider_id
    assert policy.provider_id != "opencode"


@pytest.mark.parametrize(
    "configured_model",
    [
        None,
        "",
        "openai",
        "opencode/gpt-5",
        "OpenAI/gpt-5",
        "openai/",
        "/gpt-5",
        "openai/gpt-5/extra",
        "openai/gpt 5",
        " openai/gpt-5",
        "openai/gpt-5\n",
        f"openai/{'x' * 129}",
    ],
)
def test_opencode_rejects_unbounded_ambiguous_or_self_owned_models(
    configured_model: object,
) -> None:
    """Loose parsing would guess ownership or permit an OpenCode quota source."""
    assert select_opencode_capacity_policy(configured_model) is None
