"""Pure provider-capacity declarations, negotiation, and policy selection.

This module performs no discovery, configuration reads, credential access,
network work, refresh scheduling, or lifecycle publication.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from .capacity_types import (
    MAX_LANES_PER_OBSERVATION,
    CapacityEvidenceClass,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneKey,
    SourceKey,
)
from .provider_contracts import (
    AdapterIdentifier,
    CapabilityIdentifier,
    CapacityLaneDescriptor,
    CapacitySourceDescriptor,
    ContractValidationError,
    ProviderIdentifier,
    SourceInstanceIdentifier,
    negotiate_capacity_source_contract,
)

_DECLARATION_SOURCE = SourceKey(
    "capacity",
    "policy",
    "static",
    "remote_quota_windows",
)
_OPENCODE_MODEL = re.compile(
    r"(?P<provider>[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*)/"
    r"(?P<model>[A-Za-z0-9][A-Za-z0-9._~:-]{0,127})\Z"
)
_OPENCODE_UPSTREAM_PROFILES = MappingProxyType(
    {
        "anthropic": "anthropic-console-api",
        "github-copilot": "github-copilot",
        "google": "google-cloud-api",
        "openai": "openai-api-organization",
    }
)


class ProviderCapacityPolicyError(ValueError):
    """A static provider-capacity declaration failed closed."""


class CapacityPolicyState(str, Enum):
    OBSERVABLE = "observable"
    DETAIL_ONLY = "detail_only"
    LINK_ONLY = "link_only"
    UNSUPPORTED = "unsupported"
    UPSTREAM_DELEGATED = "upstream_delegated"


@dataclass(frozen=True, slots=True)
class CapacitySemanticLane:
    """Provider-owned pool identity with product-owned display semantics."""

    pool_id: str
    opaque_scope: str
    model: str | None
    window: str
    effect: QuotaEffect
    semantic_name: str
    horizon: QuotaHorizon
    bindable: bool

    def __post_init__(self) -> None:
        try:
            key = QuotaLaneKey(
                source=_DECLARATION_SOURCE,
                opaque_scope=self.opaque_scope,
                pool=self.pool_id,
                model=self.model,
                window=self.window,
                effect=self.effect,
            )
            CapacityLaneDescriptor(
                key=key,
                semantic_name=self.semantic_name,
                horizon=self.horizon,
                bindable=self.bindable,
            )
        except (TypeError, ValueError) as error:
            raise ProviderCapacityPolicyError("invalid semantic capacity lane") from error

    def descriptor_for(self, source: SourceKey) -> CapacityLaneDescriptor:
        return CapacityLaneDescriptor(
            key=QuotaLaneKey(
                source=source,
                opaque_scope=self.opaque_scope,
                pool=self.pool_id,
                model=self.model,
                window=self.window,
                effect=self.effect,
            ),
            semantic_name=self.semantic_name,
            horizon=self.horizon,
            bindable=self.bindable,
        )


@dataclass(frozen=True, slots=True)
class AccountCapacitySourceRegistration:
    """One literal provider, authentication, pool, and lane policy."""

    capacity_profile_id: str
    provider_id: str
    evidence_class: CapacityEvidenceClass
    auth_modes: tuple[str, ...]
    lanes: tuple[CapacitySemanticLane, ...]
    opt_in_required: bool
    state: CapacityPolicyState
    source: SourceKey | None

    def __post_init__(self) -> None:
        try:
            CapabilityIdentifier(self.capacity_profile_id)
            ProviderIdentifier(self.provider_id)
            if (
                not isinstance(self.evidence_class, CapacityEvidenceClass)
                or type(self.auth_modes) is not tuple
                or not self.auth_modes
                or len(self.auth_modes) != len(set(self.auth_modes))
                or type(self.lanes) is not tuple
                or len(self.lanes) > MAX_LANES_PER_OBSERVATION
                or not all(type(lane) is CapacitySemanticLane for lane in self.lanes)
                or type(self.opt_in_required) is not bool
                or not isinstance(self.state, CapacityPolicyState)
                or (self.source is not None and not isinstance(self.source, SourceKey))
            ):
                raise ProviderCapacityPolicyError("invalid provider capacity policy")
            for auth_mode in self.auth_modes:
                CapabilityIdentifier(auth_mode)
        except ContractValidationError as error:
            raise ProviderCapacityPolicyError("invalid provider capacity policy") from error

        if self.source is not None and self.source.provider_id != self.provider_id:
            raise ProviderCapacityPolicyError("source provider mismatch")
        if self.source is not None and self.source.capability_id != "remote_quota_windows":
            raise ProviderCapacityPolicyError("invalid capacity source capability")

        lane_identities = tuple(
            (lane.pool_id, lane.opaque_scope, lane.model, lane.window, lane.effect)
            for lane in self.lanes
        )
        if len(lane_identities) != len(set(lane_identities)):
            raise ProviderCapacityPolicyError("duplicate semantic capacity lane")

        official = {
            CapacityEvidenceClass.OFFICIAL_LOCAL,
            CapacityEvidenceClass.OFFICIAL_API,
            CapacityEvidenceClass.OFFICIAL_ADMIN_API,
        }
        if self.state is CapacityPolicyState.OBSERVABLE and not (
            self.evidence_class in official
            and self.source is not None
            and self.lanes
            and any(lane.bindable for lane in self.lanes)
        ):
            raise ProviderCapacityPolicyError("invalid observable capacity policy")
        if self.state is CapacityPolicyState.DETAIL_ONLY and not (
            self.evidence_class in official
            and self.source is not None
            and self.lanes
            and not any(lane.bindable for lane in self.lanes)
        ):
            raise ProviderCapacityPolicyError("invalid detail-only capacity policy")
        if self.state is CapacityPolicyState.LINK_ONLY and not (
            self.evidence_class is CapacityEvidenceClass.UI_LINK_ONLY
            and self.source is None
            and self.lanes
            and not any(lane.bindable for lane in self.lanes)
        ):
            raise ProviderCapacityPolicyError("invalid link-only capacity policy")
        if self.state is CapacityPolicyState.UNSUPPORTED and not (
            self.evidence_class is CapacityEvidenceClass.UNSUPPORTED
            and self.source is None
            and not any(lane.bindable for lane in self.lanes)
        ):
            raise ProviderCapacityPolicyError("invalid unsupported capacity policy")
        if self.state is CapacityPolicyState.UPSTREAM_DELEGATED and not (
            self.provider_id == "opencode"
            and self.evidence_class is CapacityEvidenceClass.UNSUPPORTED
            and self.source is None
            and not self.lanes
            and self.auth_modes == ("upstream-config",)
        ):
            raise ProviderCapacityPolicyError("invalid delegated capacity policy")
        if (
            self.evidence_class
            in {
                CapacityEvidenceClass.OFFICIAL_API,
                CapacityEvidenceClass.OFFICIAL_ADMIN_API,
            }
            and not self.opt_in_required
        ):
            raise ProviderCapacityPolicyError("network capacity policy requires opt-in")

    @property
    def pool_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(lane.pool_id for lane in self.lanes))

    @property
    def profile_id(self) -> str:
        """Compatibility name for policy selection and display projection."""
        return self.capacity_profile_id

    @property
    def capability_id(self) -> str:
        """The only capacity capability this declaration may negotiate."""
        return "remote_quota_windows"


ProviderCapacityPolicy = AccountCapacitySourceRegistration


def _lane(
    pool_id: str,
    window: str,
    semantic_name: str,
    *,
    scope: str = "all",
    model: str | None = None,
    horizon: QuotaHorizon = QuotaHorizon.OTHER,
    effect: QuotaEffect = QuotaEffect.ALL_WORKLOADS,
    bindable: bool = False,
) -> CapacitySemanticLane:
    # `model` exists only so a provider with per-model sub-caps (Claude's
    # weekly Opus and Sonnet ceilings) can be declared here rather than
    # invented at runtime. QuotaLaneKey rejects the two ways that could go
    # wrong -- a MODEL lane without a model, or a model on any other effect.
    return CapacitySemanticLane(
        pool_id=pool_id,
        opaque_scope=scope,
        model=model,
        window=window,
        effect=effect,
        semantic_name=semantic_name,
        horizon=horizon,
        bindable=bindable,
    )


def _source(provider_id: str, adapter_id: str, source_instance_id: str) -> SourceKey:
    return SourceKey(
        provider_id,
        adapter_id,
        source_instance_id,
        "remote_quota_windows",
    )


# Complete reviewed policy matrix. A declared source identity is inert until an
# exact runtime source registration exists and successfully negotiates it.
_PROVIDER_CAPACITY_POLICIES = (
    ProviderCapacityPolicy(
        "openai-codex-consumer",
        "codex",
        CapacityEvidenceClass.OFFICIAL_LOCAL,
        ("chatgpt", "chatgpt-access-token"),
        (
            _lane(
                "codex-chatgpt-plan",
                "five-hour",
                "5-hour window",
                horizon=QuotaHorizon.SHORT,
                bindable=True,
            ),
            _lane(
                "codex-chatgpt-plan",
                "weekly",
                "Weekly window",
                horizon=QuotaHorizon.LONG,
                bindable=True,
            ),
        ),
        False,
        CapacityPolicyState.OBSERVABLE,
        _source("codex", "quota", "local"),
    ),
    ProviderCapacityPolicy(
        "openai-api-organization",
        "openai",
        CapacityEvidenceClass.OFFICIAL_API,
        ("api-key", "service-account"),
        (
            _lane(
                "openai-api-organization",
                "billing-month",
                "Organization billing month",
            ),
        ),
        True,
        CapacityPolicyState.DETAIL_ONLY,
        _source("openai", "usage-api", "organization"),
    ),
    # A consumer Claude subscription publishes several ceilings at once, and
    # the per-model weekly sub-caps are the ones that actually stop work. They
    # are declared as separate MODEL lanes rather than folded into the weekly
    # window: a weekly-Opus reading says nothing about a Sonnet session, and
    # the authority layer can only refuse a lane it can tell apart.
    # OFFICIAL_API, not OFFICIAL_LOCAL: this is read from Anthropic's own
    # endpoint with the user's subscription credential, so it is opt-in.
    ProviderCapacityPolicy(
        "anthropic-consumer",
        "claude",
        CapacityEvidenceClass.OFFICIAL_API,
        ("consumer",),
        (
            _lane(
                "claude-consumer-plan",
                "five-hour",
                "5-hour",
                horizon=QuotaHorizon.SHORT,
                bindable=True,
            ),
            _lane(
                "claude-consumer-plan",
                "weekly",
                "Weekly",
                horizon=QuotaHorizon.LONG,
                bindable=True,
            ),
            _lane(
                "claude-consumer-plan",
                "weekly",
                "Weekly Opus",
                model="opus",
                horizon=QuotaHorizon.LONG,
                effect=QuotaEffect.MODEL,
                bindable=True,
            ),
            _lane(
                "claude-consumer-plan",
                "weekly",
                "Weekly Sonnet",
                model="sonnet",
                horizon=QuotaHorizon.LONG,
                effect=QuotaEffect.MODEL,
                bindable=True,
            ),
        ),
        True,
        CapacityPolicyState.OBSERVABLE,
        _source("claude", "quota", "oauth"),
    ),
    ProviderCapacityPolicy(
        "anthropic-team-enterprise",
        "claude",
        CapacityEvidenceClass.OFFICIAL_ADMIN_API,
        ("team", "enterprise"),
        (
            _lane("claude-team-seat", "billing-month", "Team seat billing month"),
            _lane(
                "claude-enterprise-seat",
                "billing-month",
                "Enterprise seat billing month",
            ),
        ),
        True,
        CapacityPolicyState.DETAIL_ONLY,
        _source("claude", "analytics-api", "organization"),
    ),
    ProviderCapacityPolicy(
        "anthropic-console-api",
        "anthropic",
        CapacityEvidenceClass.OFFICIAL_ADMIN_API,
        ("console-api-key", "console-oauth"),
        (
            _lane(
                "anthropic-console-organization",
                "billing-month",
                "Console organization billing month",
            ),
        ),
        True,
        CapacityPolicyState.DETAIL_ONLY,
        _source("anthropic", "usage-api", "organization"),
    ),
    ProviderCapacityPolicy(
        "google-gemini-code-assist",
        "google",
        CapacityEvidenceClass.OFFICIAL_LOCAL,
        ("google-ai", "code-assist", "gemini-enterprise"),
        (
            _lane(
                "google-ai-plan",
                "daily",
                "Google AI plan daily window",
                horizon=QuotaHorizon.SHORT,
                bindable=True,
            ),
            _lane(
                "gemini-enterprise",
                "daily",
                "Gemini Enterprise daily window",
                horizon=QuotaHorizon.SHORT,
                bindable=True,
            ),
        ),
        False,
        CapacityPolicyState.OBSERVABLE,
        _source("google", "quota", "gemini-plan"),
    ),
    ProviderCapacityPolicy(
        "google-antigravity",
        "google",
        CapacityEvidenceClass.UI_LINK_ONLY,
        ("antigravity-plan", "antigravity-enterprise"),
        (
            _lane(
                "google-antigravity-plan",
                "daily",
                "Antigravity plan daily window",
                horizon=QuotaHorizon.SHORT,
            ),
            _lane(
                "google-antigravity-enterprise",
                "daily",
                "Antigravity enterprise daily window",
                horizon=QuotaHorizon.SHORT,
            ),
        ),
        False,
        CapacityPolicyState.LINK_ONLY,
        None,
    ),
    ProviderCapacityPolicy(
        "google-cloud-api",
        "google",
        CapacityEvidenceClass.OFFICIAL_API,
        ("google-cloud-api-key", "google-cloud-oauth", "service-account"),
        (_lane("google-cloud-project", "daily", "Google Cloud project daily quota"),),
        True,
        CapacityPolicyState.DETAIL_ONLY,
        _source("google", "quota", "cloud-api"),
    ),
    ProviderCapacityPolicy(
        "github-copilot",
        "github",
        CapacityEvidenceClass.UI_LINK_ONLY,
        ("copilot-user", "copilot-organization"),
        (
            _lane(
                "github-copilot-user",
                "billing-month",
                "User premium requests",
                scope="premium-requests",
                effect=QuotaEffect.FEATURE,
            ),
            _lane(
                "github-copilot-organization",
                "billing-month",
                "Organization premium requests",
                scope="premium-requests",
                effect=QuotaEffect.FEATURE,
            ),
        ),
        False,
        CapacityPolicyState.LINK_ONLY,
        None,
    ),
    ProviderCapacityPolicy(
        "cursor-team-organization",
        "cursor",
        CapacityEvidenceClass.OFFICIAL_API,
        ("team-api", "organization-api"),
        (
            _lane("cursor-team", "billing-month", "Team billing month"),
            _lane(
                "cursor-organization",
                "billing-month",
                "Organization billing month",
            ),
        ),
        True,
        CapacityPolicyState.DETAIL_ONLY,
        _source("cursor", "account-api", "organization"),
    ),
    ProviderCapacityPolicy(
        "devin-windsurf-team",
        "devin",
        CapacityEvidenceClass.OFFICIAL_API,
        ("devin-team-api", "windsurf-team-api"),
        (
            _lane(
                "devin-team",
                "billing-month",
                "Devin team credits",
                scope="credits",
                effect=QuotaEffect.FEATURE,
            ),
            _lane(
                "windsurf-team",
                "billing-month",
                "Windsurf team credits",
                scope="credits",
                effect=QuotaEffect.FEATURE,
            ),
        ),
        True,
        CapacityPolicyState.DETAIL_ONLY,
        _source("devin", "account-api", "team"),
    ),
    ProviderCapacityPolicy(
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


@dataclass(frozen=True, slots=True)
class NegotiatedProviderCapacityPolicy:
    policy: ProviderCapacityPolicy
    negotiated_source: object | None
    descriptor: CapacitySourceDescriptor | None

    def __post_init__(self) -> None:
        from .providers import NegotiatedProviderSource

        if not isinstance(self.policy, ProviderCapacityPolicy):
            raise ProviderCapacityPolicyError("invalid negotiated capacity policy")
        if self.negotiated_source is None and self.descriptor is None:
            return
        if self.policy.source is None:
            if self.negotiated_source is not None or self.descriptor is not None:
                raise ProviderCapacityPolicyError("invalid negotiated capacity policy")
            return
        if not (
            type(self.negotiated_source) is NegotiatedProviderSource
            and isinstance(self.descriptor, CapacitySourceDescriptor)
            and self.negotiated_source.source_key == self.policy.source
            and self.descriptor.source == self.policy.source
        ):
            raise ProviderCapacityPolicyError("invalid negotiated capacity policy")


def provider_capacity_policies() -> tuple[ProviderCapacityPolicy, ...]:
    """Return the immutable literal provider-capacity policy table."""
    return _PROVIDER_CAPACITY_POLICIES


def select_provider_capacity_policy(
    provider_id: object,
    auth_mode: object,
) -> ProviderCapacityPolicy | None:
    """Select only an exact provider and authentication-mode declaration."""
    try:
        if not isinstance(provider_id, str) or not isinstance(auth_mode, str):
            return None
        ProviderIdentifier(provider_id)
        CapabilityIdentifier(auth_mode)
    except ContractValidationError:
        return None
    matches = tuple(
        policy
        for policy in provider_capacity_policies()
        if policy.provider_id == provider_id and auth_mode in policy.auth_modes
    )
    if len(matches) > 1:
        raise ProviderCapacityPolicyError("ambiguous provider capacity policy")
    return matches[0] if matches else None


def select_opencode_capacity_policy(
    configured_model: object,
) -> ProviderCapacityPolicy | None:
    """Delegate OpenCode capacity to one exact bounded upstream model prefix."""
    if (
        not isinstance(configured_model, str)
        or not configured_model.isascii()
        or len(configured_model) > 161
    ):
        return None
    match = _OPENCODE_MODEL.fullmatch(configured_model)
    if match is None:
        return None
    profile_id = _OPENCODE_UPSTREAM_PROFILES.get(match.group("provider"))
    if profile_id is None:
        return None
    policies = tuple(
        policy
        for policy in provider_capacity_policies()
        if policy.profile_id == profile_id and policy.provider_id != "opencode"
    )
    return policies[0] if len(policies) == 1 else None


def negotiate_provider_capacity_policies(
    available_sources: tuple[object, ...],
) -> tuple[NegotiatedProviderCapacityPolicy, ...]:
    """Bind every actual capacity policy to exactly one exact registry source."""
    from .providers import NegotiatedProviderSource

    if type(available_sources) is not tuple or not all(
        type(source) is NegotiatedProviderSource for source in available_sources
    ):
        raise ProviderCapacityPolicyError("invalid available capacity sources")
    policies = provider_capacity_policies()
    declared_sources = tuple(policy.source for policy in policies if policy.source is not None)
    if len(declared_sources) != len(set(declared_sources)):
        raise ProviderCapacityPolicyError("duplicate declared capacity source")

    capacity_capability = CapabilityIdentifier("remote_quota_windows")
    actual_sources = tuple(
        source
        for source in available_sources
        if getattr(source, "declared_capability_id", None) == capacity_capability
    )
    if any(
        sum(policy.source == source.source_key for policy in policies) != 1
        for source in actual_sources
    ):
        raise ProviderCapacityPolicyError("capacity source has no exact policy")

    negotiated: list[NegotiatedProviderCapacityPolicy] = []
    for policy in policies:
        if policy.source is None:
            negotiated.append(NegotiatedProviderCapacityPolicy(policy, None, None))
            continue
        exact_available = tuple(
            source for source in actual_sources if source.source_key == policy.source
        )
        if not exact_available:
            negotiated.append(NegotiatedProviderCapacityPolicy(policy, None, None))
            continue
        try:
            source = negotiate_capacity_source_contract(policy.source, available_sources)
        except ContractValidationError as error:
            raise ProviderCapacityPolicyError(str(error)) from error
        capability = source.negotiated_capability
        if capability is None:
            raise ProviderCapacityPolicyError("capacity source is not negotiated")
        descriptor = CapacitySourceDescriptor(
            provider_id=ProviderIdentifier(policy.source.provider_id),
            adapter_id=AdapterIdentifier(policy.source.adapter_id),
            source_instance_id=SourceInstanceIdentifier(
                policy.source.source_instance_id
            ),
            capability_id=CapabilityIdentifier(policy.source.capability_id),
            capability_version=capability.version,
            lanes=tuple(lane.descriptor_for(policy.source) for lane in policy.lanes),
        )
        negotiated.append(
            NegotiatedProviderCapacityPolicy(policy, source, descriptor)
        )
    return tuple(negotiated)


__all__ = [
    "AccountCapacitySourceRegistration",
    "CapacityPolicyState",
    "CapacitySemanticLane",
    "NegotiatedProviderCapacityPolicy",
    "ProviderCapacityPolicy",
    "ProviderCapacityPolicyError",
    "negotiate_provider_capacity_policies",
    "provider_capacity_policies",
    "select_opencode_capacity_policy",
    "select_provider_capacity_policy",
]
