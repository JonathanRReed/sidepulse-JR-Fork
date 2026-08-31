"""Pure validation and negotiation for static first-party provider contracts.

The contract is deliberately data-only. It does not discover adapters, import
plugins, invoke provider code, or grant provider mutation authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from .capacity_types import (
    MAX_LANES_PER_OBSERVATION,
    MAX_SEMANTIC_NAME_LENGTH,
    CapacitySourceHealth,
    CapacityValue,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneKey,
    QuotaLaneObservation,
    ResetFact,
    SourceKey,
)

CONTRACT_SCHEMA_MAJOR = 1
MAX_IDENTIFIER_LENGTH = 64
MAX_CAPABILITY_DECLARATIONS = 32
MAX_VERSIONS_PER_CAPABILITY = 8
MAX_UNKNOWN_FIELDS = 16
MAX_DIAGNOSTICS = 8
MAX_VERSION_COMPONENT = 65_535

_SLUG_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_OPAQUE_SOURCE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~:-]*\Z")


class ContractValidationError(ValueError):
    """A bounded provider declaration failed closed."""


@dataclass(frozen=True, slots=True)
class _BoundedIdentifier:
    value: str

    _pattern: ClassVar[re.Pattern[str]] = _SLUG_IDENTIFIER

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, str)
            or not 1 <= len(self.value) <= MAX_IDENTIFIER_LENGTH
            or self._pattern.fullmatch(self.value) is None
        ):
            raise ContractValidationError("invalid identifier")


class ProviderIdentifier(_BoundedIdentifier):
    """Bounded static provider identifier."""


class AdapterIdentifier(_BoundedIdentifier):
    """Bounded static adapter identifier."""


class CapabilityIdentifier(_BoundedIdentifier):
    """Bounded capability identifier, including inert future identifiers."""


class DiagnosticIdentifier(_BoundedIdentifier):
    """Bounded product-owned diagnostic identifier."""


class SourceInstanceIdentifier(_BoundedIdentifier):
    """Opaque source token whose internal separators have no contract meaning."""

    _pattern = _OPAQUE_SOURCE_IDENTIFIER


@dataclass(frozen=True, order=True, slots=True)
class SchemaVersion:
    major: int
    minor: int

    def __post_init__(self) -> None:
        if (
            type(self.major) is not int
            or type(self.minor) is not int
            or not 1 <= self.major <= MAX_VERSION_COMPONENT
            or not 0 <= self.minor <= MAX_VERSION_COMPONENT
        ):
            raise ContractValidationError("invalid version")


class CapabilityAuthority(str, Enum):
    DISCOVERY = "discovery"
    OBSERVATION = "observation"
    MUTATION = "mutation"


class ProductCapability(str, Enum):
    """Product-facing capabilities declared independently of adapters."""

    LIFECYCLE = "lifecycle"
    QUESTIONS = "questions"
    ANSWERING = "answering"
    USAGE = "usage"
    COSTS = "costs"
    RESET_FORECASTS = "reset_forecasts"
    RESET_FORECAST = "reset_forecasts"
    REMOTE_OBSERVATION = "remote_observation"
    TRANSCRIPT_FALLBACK = "transcript_fallback"
    INVOCATION_SCOPED_MONITORING = "invocation_scoped_monitoring"
    INVOCATION_MONITORING = "invocation_scoped_monitoring"


_PRODUCT_CAPABILITY_ORDER = tuple(ProductCapability)


class LocalRuntimeSurfaceIdentifier(_BoundedIdentifier):
    """Bounded name for a product-owned local runtime surface."""


@dataclass(frozen=True, slots=True)
class ProductCapabilityBinding:
    """One exact low-level capability or one named local runtime surface."""

    low_level_capability_id: CapabilityIdentifier | None = None
    capability_version: SchemaVersion | None = None
    local_runtime_surface: LocalRuntimeSurfaceIdentifier | None = None

    def __post_init__(self) -> None:
        has_low_level = self.low_level_capability_id is not None
        has_local = self.local_runtime_surface is not None
        if has_low_level == has_local:
            raise ContractValidationError("binding must name exactly one surface")
        if has_low_level and not isinstance(
            self.low_level_capability_id, CapabilityIdentifier
        ):
            raise ContractValidationError("invalid low-level capability binding")
        if has_local and not isinstance(
            self.local_runtime_surface, LocalRuntimeSurfaceIdentifier
        ):
            raise ContractValidationError("invalid local runtime surface binding")
        if has_local and self.capability_version is not None:
            raise ContractValidationError("local surface cannot have a capability version")
        if self.capability_version is not None and not isinstance(
            self.capability_version, SchemaVersion
        ):
            raise ContractValidationError("invalid capability binding version")

    @classmethod
    def low_level(
        cls,
        capability_id: str | CapabilityIdentifier,
        version: SchemaVersion | None = None,
    ) -> ProductCapabilityBinding:
        if isinstance(capability_id, CapabilityIdentifier):
            identifier = capability_id
        else:
            identifier = CapabilityIdentifier(capability_id)
        return cls(
            low_level_capability_id=identifier,
            capability_version=version,
        )

    @classmethod
    def local(
        cls,
        runtime_surface: str | LocalRuntimeSurfaceIdentifier,
    ) -> ProductCapabilityBinding:
        if isinstance(runtime_surface, LocalRuntimeSurfaceIdentifier):
            identifier = runtime_surface
        else:
            identifier = LocalRuntimeSurfaceIdentifier(runtime_surface)
        return cls(local_runtime_surface=identifier)

    @property
    def capability_id(self) -> CapabilityIdentifier | None:
        """Compatibility alias for the exact low-level identifier."""
        return self.low_level_capability_id

    @property
    def runtime_surface(self) -> LocalRuntimeSurfaceIdentifier | None:
        """Compatibility alias for the named local surface."""
        return self.local_runtime_surface


@dataclass(frozen=True, slots=True)
class ProductCapabilityDeclaration:
    """Explicit yes/no support for one product capability."""

    capability: ProductCapability
    supported: bool
    binding: ProductCapabilityBinding | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.capability, ProductCapability):
            raise ContractValidationError("invalid product capability")
        if type(self.supported) is not bool:
            raise ContractValidationError("product support must be a boolean")
        if self.supported and not isinstance(self.binding, ProductCapabilityBinding):
            raise ContractValidationError("supported capability requires a binding")
        if not self.supported and self.binding is not None:
            raise ContractValidationError("unsupported capability cannot have a binding")

    @property
    def support(self) -> bool:
        """Readable alias for the explicit support decision."""
        return self.supported

    @property
    def is_supported(self) -> bool:
        """Boolean spelling useful to callers rendering capability state."""
        return self.supported

    @property
    def capability_id(self) -> ProductCapability:
        """Stable product capability identifier."""
        return self.capability


@dataclass(frozen=True, slots=True)
class ProductCapabilityInvocation:
    """Exact identity used when invoking a supported product capability."""

    product_capability: ProductCapability
    provider_id: ProviderIdentifier
    adapter_id: AdapterIdentifier
    source_instance_id: SourceInstanceIdentifier
    capability_id: CapabilityIdentifier | None = None
    capability_version: SchemaVersion | None = None
    local_runtime_surface: LocalRuntimeSurfaceIdentifier | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.product_capability, ProductCapability):
            raise ContractValidationError("invalid product capability invocation")
        if not (
            isinstance(self.provider_id, ProviderIdentifier)
            and isinstance(self.adapter_id, AdapterIdentifier)
            and isinstance(self.source_instance_id, SourceInstanceIdentifier)
        ):
            raise ContractValidationError("invalid product capability invocation")
        ProductCapabilityBinding(
            low_level_capability_id=self.capability_id,
            capability_version=self.capability_version,
            local_runtime_surface=self.local_runtime_surface,
        )
        if self.capability_id is not None:
            allowed_bindings = _PRODUCT_LOW_LEVEL_BINDINGS.get(
                self.product_capability, frozenset()
            )
            definition = _CAPABILITY_DEFINITIONS.get(self.capability_id)
            if self.capability_id not in allowed_bindings or definition is None:
                raise ContractValidationError("product capability binding is not allowed")
            if (
                definition.authority is CapabilityAuthority.MUTATION
                or self.capability_version not in definition.versions
            ):
                raise ContractValidationError("product capability binding is not allowed")


class ContractStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED_SCHEMA_MAJOR = "unsupported_schema_major"
    UNSUPPORTED_PROVIDER = "unsupported_provider"
    UNSUPPORTED_ADAPTER = "unsupported_adapter"


@dataclass(frozen=True, slots=True)
class _CapabilityDefinition:
    identifier: CapabilityIdentifier
    authority: CapabilityAuthority
    versions: frozenset[SchemaVersion]


@dataclass(frozen=True, slots=True)
class NegotiatedCapability:
    identifier: CapabilityIdentifier
    version: SchemaVersion
    authority: CapabilityAuthority


@dataclass(frozen=True, slots=True)
class ContractDiagnostic:
    identifier: DiagnosticIdentifier
    count: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.identifier, DiagnosticIdentifier)
            or type(self.count) is not int
            or not 1 <= self.count <= max(MAX_UNKNOWN_FIELDS, MAX_CAPABILITY_DECLARATIONS)
        ):
            raise ContractValidationError("invalid diagnostic count")


@dataclass(frozen=True, slots=True)
class ActionIdentity:
    """Exact action target components, including the opaque source instance."""

    provider_id: ProviderIdentifier
    adapter_id: AdapterIdentifier
    source_instance_id: SourceInstanceIdentifier
    capability_id: CapabilityIdentifier

    def __post_init__(self) -> None:
        if not (
            isinstance(self.provider_id, ProviderIdentifier)
            and isinstance(self.adapter_id, AdapterIdentifier)
            and isinstance(self.source_instance_id, SourceInstanceIdentifier)
            and isinstance(self.capability_id, CapabilityIdentifier)
        ):
            raise ContractValidationError("invalid action identity")


@dataclass(frozen=True, slots=True)
class NegotiatedProviderContract:
    schema_version: SchemaVersion
    provider_id: ProviderIdentifier
    adapter_id: AdapterIdentifier
    source_instance_id: SourceInstanceIdentifier
    status: ContractStatus
    discovery_capabilities: tuple[NegotiatedCapability, ...] = ()
    observation_capabilities: tuple[NegotiatedCapability, ...] = ()
    compatible_mutation_capabilities: tuple[NegotiatedCapability, ...] = ()
    diagnostics: tuple[ContractDiagnostic, ...] = ()
    product_capabilities: tuple[ProductCapabilityDeclaration, ...] = ()

    @property
    def observation_invocation_allowed(self) -> bool:
        """Whether compatible read work exists for a supported first-party source."""
        return self.status in {ContractStatus.SUPPORTED, ContractStatus.PARTIAL} and bool(
            self.discovery_capabilities or self.observation_capabilities
        )

    def action_identity_for(self, capability_id: str) -> ActionIdentity:
        """Build an exact read-side target only after successful negotiation.

        Compatible mutation declarations are intentionally excluded. This pure
        contract has no API that grants provider mutation authority.
        """
        identifier = CapabilityIdentifier(capability_id)
        read_capabilities = (*self.discovery_capabilities, *self.observation_capabilities)
        if not any(capability.identifier == identifier for capability in read_capabilities):
            raise ContractValidationError("capability is not negotiated")
        return ActionIdentity(
            provider_id=self.provider_id,
            adapter_id=self.adapter_id,
            source_instance_id=self.source_instance_id,
            capability_id=identifier,
        )

    def product_capability(
        self, capability: ProductCapability | str
    ) -> ProductCapabilityDeclaration:
        """Return the explicit product declaration, including absent support."""
        try:
            product_capability = (
                capability
                if isinstance(capability, ProductCapability)
                else ProductCapability(capability)
            )
        except (TypeError, ValueError) as error:
            raise ContractValidationError("invalid product capability") from error
        for declaration in self.product_capabilities:
            if declaration.capability is product_capability:
                return declaration
        return ProductCapabilityDeclaration(product_capability, supported=False)

    def product_invocation_for(
        self, capability: ProductCapability | str
    ) -> ProductCapabilityInvocation:
        """Resolve one supported product capability to its exact source identity."""
        declaration = self.product_capability(capability)
        if not declaration.supported or declaration.binding is None:
            raise ContractValidationError("product capability is not supported")
        binding = declaration.binding
        if binding.low_level_capability_id is not None:
            return ProductCapabilityInvocation(
                product_capability=declaration.capability,
                provider_id=self.provider_id,
                adapter_id=self.adapter_id,
                source_instance_id=self.source_instance_id,
                capability_id=binding.low_level_capability_id,
                capability_version=binding.capability_version,
            )
        return ProductCapabilityInvocation(
            product_capability=declaration.capability,
            provider_id=self.provider_id,
            adapter_id=self.adapter_id,
            source_instance_id=self.source_instance_id,
            local_runtime_surface=binding.local_runtime_surface,
        )


def _definition(
    identifier: str,
    authority: CapabilityAuthority,
    *versions: tuple[int, int],
) -> _CapabilityDefinition:
    return _CapabilityDefinition(
        identifier=CapabilityIdentifier(identifier),
        authority=authority,
        versions=frozenset(SchemaVersion(major, minor) for major, minor in versions),
    )


# This is the complete static v1 host schema. It is not extended from files,
# entry points, environment variables, or provider input.
_CAPABILITY_DEFINITIONS = {
    definition.identifier: definition
    for definition in (
        _definition("live_agent_events", CapabilityAuthority.OBSERVATION, (1, 0), (1, 1)),
        _definition("actionable_requests", CapabilityAuthority.OBSERVATION, (1, 0)),
        _definition("direct_navigation", CapabilityAuthority.OBSERVATION, (1, 0)),
        _definition("transcript_usage", CapabilityAuthority.DISCOVERY, (1, 0)),
        _definition("remote_quota_windows", CapabilityAuthority.OBSERVATION, (1, 0)),
        _definition("reset_metadata", CapabilityAuthority.OBSERVATION, (1, 0)),
        _definition("account_identity", CapabilityAuthority.OBSERVATION, (1, 0)),
        _definition("history_attribution", CapabilityAuthority.OBSERVATION, (1, 0)),
        _definition("account_switching", CapabilityAuthority.MUTATION, (1, 0)),
    )
}

# Product semantics are an allowlist, not a projection of low-level authority.
# In particular, the mutation-only account_switching capability is intentionally
# absent from every product mapping. A future product action must receive its
# own reviewed declaration or a product-owned local surface.
_PRODUCT_LOW_LEVEL_BINDINGS: dict[
    ProductCapability, frozenset[CapabilityIdentifier]
] = {
    ProductCapability.LIFECYCLE: frozenset(
        {CapabilityIdentifier("live_agent_events")}
    ),
    ProductCapability.QUESTIONS: frozenset(
        {CapabilityIdentifier("actionable_requests")}
    ),
    ProductCapability.ANSWERING: frozenset(),
    ProductCapability.USAGE: frozenset(
        {
            CapabilityIdentifier("transcript_usage"),
            CapabilityIdentifier("remote_quota_windows"),
        }
    ),
    ProductCapability.COSTS: frozenset(),
    ProductCapability.RESET_FORECASTS: frozenset(
        {CapabilityIdentifier("reset_metadata")}
    ),
    ProductCapability.REMOTE_OBSERVATION: frozenset(),
    ProductCapability.TRANSCRIPT_FALLBACK: frozenset(
        {CapabilityIdentifier("transcript_usage")}
    ),
    ProductCapability.INVOCATION_SCOPED_MONITORING: frozenset(),
}

_TRANSCRIPT_ADAPTER = AdapterIdentifier("transcripts")
_ANSWER_IN_PLACE_SURFACE = LocalRuntimeSurfaceIdentifier("local.answer_in_place")
_INVOCATION_MONITORING_SURFACE = LocalRuntimeSurfaceIdentifier(
    "local.invocation_scoped_monitoring"
)

# These pairs describe known in-tree source kinds only. They do not load or
# identify an implementation. Adapter integration is a later reviewed tranche.
_FIRST_PARTY_ADAPTERS = {
    ProviderIdentifier("codex"): frozenset(
        {AdapterIdentifier("hooks"), AdapterIdentifier("transcripts"), AdapterIdentifier("quota")}
    ),
    ProviderIdentifier("claude"): frozenset(
        {AdapterIdentifier("hooks"), AdapterIdentifier("transcripts"), AdapterIdentifier("quota")}
    ),
    ProviderIdentifier("devin"): frozenset({AdapterIdentifier("hooks")}),
    ProviderIdentifier("grok"): frozenset({AdapterIdentifier("hooks")}),
    ProviderIdentifier("cursor"): frozenset({AdapterIdentifier("hooks")}),
    ProviderIdentifier("hermes"): frozenset({AdapterIdentifier("hooks")}),
    ProviderIdentifier("openclaw"): frozenset({AdapterIdentifier("hooks")}),
    ProviderIdentifier("opencode"): frozenset({AdapterIdentifier("hooks")}),
    # hooks only. Antigravity's subscription ceiling is a Google plan quota
    # with no local file to read, and it stays represented by the link-only
    # google-antigravity policy rather than by a quota adapter here.
    ProviderIdentifier("antigravity"): frozenset({AdapterIdentifier("hooks")}),
    ProviderIdentifier("kiro"): frozenset({AdapterIdentifier("hooks")}),
}

_CAPACITY_CAPABILITY_IDENTIFIERS = frozenset(
    {CapabilityIdentifier("remote_quota_windows")}
)


@dataclass(frozen=True, slots=True)
class CapacityLaneDescriptor:
    """Static product-owned semantics for one exact quota lane."""

    key: QuotaLaneKey
    semantic_name: str
    horizon: QuotaHorizon
    bindable: bool

    def __post_init__(self) -> None:
        if not (
            isinstance(self.key, QuotaLaneKey)
            and isinstance(self.semantic_name, str)
            and 1 <= len(self.semantic_name) <= MAX_SEMANTIC_NAME_LENGTH
            and self.semantic_name == self.semantic_name.strip()
            and self.semantic_name.isprintable()
            and isinstance(self.horizon, QuotaHorizon)
            and type(self.bindable) is bool
        ):
            raise ContractValidationError("invalid capacity lane descriptor")
        if self.key.effect is QuotaEffect.UNKNOWN and self.bindable:
            raise ContractValidationError("unknown quota effect cannot be bindable")


@dataclass(frozen=True, slots=True)
class CapacitySourceDescriptor:
    """Static capacity declaration for one negotiated first-party source.

    Construction validates only bounded in-memory data. It never discovers or
    invokes the adapter represented by the descriptor.
    """

    provider_id: ProviderIdentifier
    adapter_id: AdapterIdentifier
    source_instance_id: SourceInstanceIdentifier
    capability_id: CapabilityIdentifier
    capability_version: SchemaVersion
    lanes: tuple[CapacityLaneDescriptor, ...]

    def __post_init__(self) -> None:
        if not (
            isinstance(self.provider_id, ProviderIdentifier)
            and isinstance(self.adapter_id, AdapterIdentifier)
            and isinstance(self.source_instance_id, SourceInstanceIdentifier)
            and isinstance(self.capability_id, CapabilityIdentifier)
            and isinstance(self.capability_version, SchemaVersion)
        ):
            raise ContractValidationError("invalid capacity source descriptor")

        definition = _CAPABILITY_DEFINITIONS.get(self.capability_id)
        if (
            self.capability_id not in _CAPACITY_CAPABILITY_IDENTIFIERS
            or definition is None
            or definition.authority is not CapabilityAuthority.OBSERVATION
            or self.capability_version not in definition.versions
        ):
            raise ContractValidationError("unsupported capacity capability version")
        if (
            self.provider_id not in _FIRST_PARTY_ADAPTERS
            or self.adapter_id not in _FIRST_PARTY_ADAPTERS[self.provider_id]
        ):
            raise ContractValidationError("unsupported capacity source")

        if type(self.lanes) is not tuple or not all(
            isinstance(lane, CapacityLaneDescriptor) for lane in self.lanes
        ):
            raise ContractValidationError("invalid capacity lanes")
        if len(self.lanes) > MAX_LANES_PER_OBSERVATION:
            raise ContractValidationError("too many capacity lanes")

        expected_source = self.source
        if any(lane.key.source != expected_source for lane in self.lanes):
            raise ContractValidationError("invalid capacity lane source")
        lane_keys = tuple(lane.key for lane in self.lanes)
        if len(lane_keys) != len(set(lane_keys)):
            raise ContractValidationError("duplicate capacity lane")

    @property
    def source(self) -> SourceKey:
        return SourceKey(
            provider_id=self.provider_id.value,
            adapter_id=self.adapter_id.value,
            source_instance_id=self.source_instance_id.value,
            capability_id=self.capability_id.value,
        )

    def build_observation(
        self,
        *,
        key: QuotaLaneKey,
        value: CapacityValue,
        reset: ResetFact,
        observed_at: float,
        source_health: CapacitySourceHealth,
        account_discriminator: str | None,
        auth_mode: str | None = None,
    ) -> QuotaLaneObservation:
        """Stamp one declared lane with its static semantics and horizon.

        ``auth_mode`` travels with the observation because an account binding
        is only exact when it names the authentication mode the reading came
        through. Without it every contract-built observation was refused
        ``auth_mode_binding_mismatch``, which made binding structurally
        impossible and left `limit=0` doing the work of a contract.
        """
        lane = next((candidate for candidate in self.lanes if candidate.key == key), None)
        if lane is None:
            raise ContractValidationError("capacity lane is not declared")
        return QuotaLaneObservation(
            key=key,
            semantic_name=lane.semantic_name,
            horizon=lane.horizon,
            value=value,
            reset=reset,
            observed_at=observed_at,
            source_health=source_health,
            account_discriminator=account_discriminator,
            auth_mode=auth_mode,
        )

_DOCUMENT_FIELDS = frozenset(
    {
        "schema_version",
        "provider_id",
        "adapter_id",
        "source_instance_id",
        "capabilities",
        "product_capabilities",
    }
)
_VERSION_FIELDS = frozenset({"major", "minor"})
_CAPABILITY_FIELDS = frozenset({"id", "versions"})
_PRODUCT_CAPABILITY_FIELDS = frozenset({"id", "supported", "binding"})
_BINDING_FIELDS = frozenset({"kind", "id", "version"})


@dataclass(slots=True)
class _UnknownFieldBudget:
    count: int = 0

    def add_mapping(self, value: dict[object, object], known_fields: frozenset[str]) -> None:
        self.count += sum(key not in known_fields for key in value)
        if self.count > MAX_UNKNOWN_FIELDS:
            raise ContractValidationError("too many unknown fields")


def _require_document(value: object) -> dict[object, object]:
    if type(value) is not dict:
        raise ContractValidationError("contract must be an object")
    return value


def _parse_version(
    value: object,
    unknown_fields: _UnknownFieldBudget,
) -> SchemaVersion:
    if type(value) is not dict:
        raise ContractValidationError("invalid version")
    unknown_fields.add_mapping(value, _VERSION_FIELDS)
    try:
        return SchemaVersion(value["major"], value["minor"])
    except KeyError as error:
        raise ContractValidationError("invalid version") from error


def _identity_envelope(
    document: dict[object, object],
) -> tuple[ProviderIdentifier, AdapterIdentifier, SourceInstanceIdentifier]:
    try:
        return (
            ProviderIdentifier(document["provider_id"]),
            AdapterIdentifier(document["adapter_id"]),
            SourceInstanceIdentifier(document["source_instance_id"]),
        )
    except KeyError as error:
        raise ContractValidationError("missing required field") from error


def _diagnostic(identifier: str, count: int = 1) -> ContractDiagnostic:
    return ContractDiagnostic(DiagnosticIdentifier(identifier), count)


def _unsupported_product_capabilities() -> tuple[ProductCapabilityDeclaration, ...]:
    return tuple(
        ProductCapabilityDeclaration(capability, supported=False)
        for capability in _PRODUCT_CAPABILITY_ORDER
    )


def _unsupported_result(
    *,
    schema_version: SchemaVersion,
    provider_id: ProviderIdentifier,
    adapter_id: AdapterIdentifier,
    source_instance_id: SourceInstanceIdentifier,
    status: ContractStatus,
) -> NegotiatedProviderContract:
    return NegotiatedProviderContract(
        schema_version=schema_version,
        provider_id=provider_id,
        adapter_id=adapter_id,
        source_instance_id=source_instance_id,
        status=status,
        diagnostics=(_diagnostic(status.value),),
        product_capabilities=_unsupported_product_capabilities(),
    )


def product_capability_document(
    declarations: tuple[ProductCapabilityDeclaration, ...],
) -> list[dict[str, object]]:
    """Serialize explicit product declarations without loading a provider."""
    if type(declarations) is not tuple:
        raise ContractValidationError("product declarations must be a tuple")
    if len(declarations) > len(_PRODUCT_CAPABILITY_ORDER):
        raise ContractValidationError("too many product capabilities")
    seen: set[ProductCapability] = set()
    rows: list[dict[str, object]] = []
    for declaration in declarations:
        if type(declaration) is not ProductCapabilityDeclaration:
            raise ContractValidationError("invalid product capability declaration")
        if declaration.capability in seen:
            raise ContractValidationError("duplicate product capability")
        seen.add(declaration.capability)
        binding: dict[str, object] | None
        if declaration.binding is None:
            binding = None
        elif declaration.binding.low_level_capability_id is not None:
            binding = {
                "kind": "low_level",
                "id": declaration.binding.low_level_capability_id.value,
                "version": (
                    None
                    if declaration.binding.capability_version is None
                    else {
                        "major": declaration.binding.capability_version.major,
                        "minor": declaration.binding.capability_version.minor,
                    }
                ),
            }
        else:
            binding = {
                "kind": "local",
                "id": declaration.binding.local_runtime_surface.value,
            }
        rows.append(
            {
                "id": declaration.capability.value,
                "supported": declaration.supported,
                "binding": binding,
            }
        )
    return rows


def provider_contract_document(
    registration: object,
    product_capabilities: tuple[ProductCapabilityDeclaration, ...] = (),
) -> dict[str, object]:
    """Build the exact built-in-dict v1 declaration for one static source."""
    from .providers import ProviderSourceRegistration

    if type(registration) is not ProviderSourceRegistration:
        raise ContractValidationError("invalid provider source registration")
    document: dict[str, object] = {
        "schema_version": {"major": CONTRACT_SCHEMA_MAJOR, "minor": 0},
        "provider_id": registration.provider_id.value,
        "adapter_id": registration.adapter_id.value,
        "source_instance_id": registration.source_instance_id.value,
        "capabilities": [
            {
                "id": capability_id.value,
                "versions": [
                    {"major": version.major, "minor": version.minor}
                    for version in versions
                ],
            }
            for capability_id, versions in registration.capability_versions
        ],
    }
    declared_rows = product_capability_document(product_capabilities)
    declared_by_id = {row["id"]: row for row in declared_rows}
    document["product_capabilities"] = [
        declared_by_id.get(
            capability.value,
            {"id": capability.value, "supported": False, "binding": None},
        )
        for capability in _PRODUCT_CAPABILITY_ORDER
    ]
    return document


def _parse_product_binding(
    value: object,
    unknown_fields: _UnknownFieldBudget,
) -> ProductCapabilityBinding:
    if type(value) is not dict:
        raise ContractValidationError("invalid product capability binding")
    unknown_fields.add_mapping(value, _BINDING_FIELDS)
    try:
        kind = value["kind"]
        identifier = value["id"]
    except KeyError as error:
        raise ContractValidationError("invalid product capability binding") from error
    if kind == "low_level":
        version_value = value.get("version")
        version = (
            None if version_value is None else _parse_version(version_value, unknown_fields)
        )
        return ProductCapabilityBinding.low_level(identifier, version)
    if kind == "local":
        if "version" in value and value["version"] is not None:
            raise ContractValidationError("local surface cannot have a capability version")
        return ProductCapabilityBinding.local(identifier)
    raise ContractValidationError("invalid product capability binding")


def _product_binding_allowed(
    capability: ProductCapability,
    binding: ProductCapabilityBinding,
    adapter_id: AdapterIdentifier,
    negotiated_by_id: dict[CapabilityIdentifier, NegotiatedCapability],
) -> bool:
    """Apply the product-to-source allowlist after structural validation."""
    if binding.local_runtime_surface is not None:
        # A local surface is product-owned and therefore does not grant
        # provider mutation authority. Some product capabilities require one
        # exact reviewed local route.
        if capability is ProductCapability.ANSWERING:
            return binding.local_runtime_surface == _ANSWER_IN_PLACE_SURFACE
        if capability is ProductCapability.INVOCATION_SCOPED_MONITORING:
            return binding.local_runtime_surface == _INVOCATION_MONITORING_SURFACE
        return capability is not ProductCapability.TRANSCRIPT_FALLBACK

    capability_id = binding.low_level_capability_id
    if capability_id is None or capability_id not in _PRODUCT_LOW_LEVEL_BINDINGS.get(
        capability, frozenset()
    ):
        return False
    negotiated_capability = negotiated_by_id.get(capability_id)
    if negotiated_capability is None or negotiated_capability.authority is CapabilityAuthority.MUTATION:
        return False
    if (
        capability is ProductCapability.TRANSCRIPT_FALLBACK
        and adapter_id != _TRANSCRIPT_ADAPTER
    ):
        return False
    return True


def _parse_product_capabilities(
    raw: dict[object, object],
    unknown_fields: _UnknownFieldBudget,
    negotiated: tuple[NegotiatedCapability, ...],
    adapter_id: AdapterIdentifier,
) -> tuple[tuple[ProductCapabilityDeclaration, ...], int, int]:
    try:
        rows = raw["product_capabilities"]
    except KeyError:
        return tuple(
            ProductCapabilityDeclaration(capability, supported=False)
            for capability in _PRODUCT_CAPABILITY_ORDER
        ), 0, 0
    if type(rows) is not list:
        raise ContractValidationError("product capabilities must be a list")
    if len(rows) > len(_PRODUCT_CAPABILITY_ORDER):
        raise ContractValidationError("too many product capabilities")

    negotiated_by_id = {capability.identifier: capability for capability in negotiated}
    declarations: dict[ProductCapability, ProductCapabilityDeclaration] = {}
    unknown_count = 0
    unsupported_count = 0
    for row in rows:
        if type(row) is not dict:
            raise ContractValidationError("product capability must be an object")
        unknown_fields.add_mapping(row, _PRODUCT_CAPABILITY_FIELDS)
        try:
            capability = ProductCapability(row["id"])
            supported = row["supported"]
            binding_value = row["binding"]
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, ValueError):
                unknown_count += 1
                continue
            raise ContractValidationError("invalid product capability") from error
        if capability in declarations:
            raise ContractValidationError("duplicate product capability")
        if type(supported) is not bool:
            raise ContractValidationError("product support must be a boolean")
        if not supported:
            if binding_value is not None:
                raise ContractValidationError("unsupported capability cannot have a binding")
            declarations[capability] = ProductCapabilityDeclaration(capability, False)
            continue
        if binding_value is None:
            raise ContractValidationError("supported capability requires a binding")
        binding = _parse_product_binding(binding_value, unknown_fields)
        if binding.low_level_capability_id is not None:
            negotiated_capability = negotiated_by_id.get(binding.low_level_capability_id)
            if negotiated_capability is None:
                unsupported_count += 1
                declarations[capability] = ProductCapabilityDeclaration(capability, False)
                continue
            if binding.capability_version is None:
                binding = ProductCapabilityBinding.low_level(
                    binding.low_level_capability_id,
                    negotiated_capability.version,
                )
            elif binding.capability_version != negotiated_capability.version:
                unsupported_count += 1
                declarations[capability] = ProductCapabilityDeclaration(capability, False)
                continue
        if not _product_binding_allowed(
            capability,
            binding,
            adapter_id=adapter_id,
            negotiated_by_id=negotiated_by_id,
        ):
            unsupported_count += 1
            declarations[capability] = ProductCapabilityDeclaration(capability, False)
            continue
        declarations[capability] = ProductCapabilityDeclaration(
            capability,
            supported=True,
            binding=binding,
        )

    ordered = tuple(
        declarations.get(capability, ProductCapabilityDeclaration(capability, False))
        for capability in _PRODUCT_CAPABILITY_ORDER
    )
    return ordered, unknown_count, unsupported_count


def negotiate_provider_contract(document: object) -> NegotiatedProviderContract:
    """Validate and negotiate one static first-party provider declaration.

    Unsupported schema majors and unknown provider or adapter identifiers stay
    visible as bounded identity envelopes. Their capability data is not read.
    Same-major additive fields and capability names are ignored and summarized
    only by static diagnostic counters.
    """
    raw = _require_document(document)
    if len(raw) > len(_DOCUMENT_FIELDS) + MAX_UNKNOWN_FIELDS:
        raise ContractValidationError("too many unknown fields")

    provider_id, adapter_id, source_instance_id = _identity_envelope(raw)
    version_fields = _UnknownFieldBudget()
    try:
        schema_version = _parse_version(raw["schema_version"], version_fields)
    except KeyError as error:
        raise ContractValidationError("missing required field") from error

    if schema_version.major != CONTRACT_SCHEMA_MAJOR:
        return _unsupported_result(
            schema_version=schema_version,
            provider_id=provider_id,
            adapter_id=adapter_id,
            source_instance_id=source_instance_id,
            status=ContractStatus.UNSUPPORTED_SCHEMA_MAJOR,
        )
    if provider_id not in _FIRST_PARTY_ADAPTERS:
        return _unsupported_result(
            schema_version=schema_version,
            provider_id=provider_id,
            adapter_id=adapter_id,
            source_instance_id=source_instance_id,
            status=ContractStatus.UNSUPPORTED_PROVIDER,
        )
    if adapter_id not in _FIRST_PARTY_ADAPTERS[provider_id]:
        return _unsupported_result(
            schema_version=schema_version,
            provider_id=provider_id,
            adapter_id=adapter_id,
            source_instance_id=source_instance_id,
            status=ContractStatus.UNSUPPORTED_ADAPTER,
        )

    unknown_fields = _UnknownFieldBudget(version_fields.count)
    unknown_fields.add_mapping(raw, _DOCUMENT_FIELDS)
    try:
        capability_rows = raw["capabilities"]
    except KeyError as error:
        raise ContractValidationError("missing required field") from error
    if type(capability_rows) is not list:
        raise ContractValidationError("capabilities must be a list")
    if len(capability_rows) > MAX_CAPABILITY_DECLARATIONS:
        raise ContractValidationError("too many capabilities")

    discovery: list[NegotiatedCapability] = []
    observation: list[NegotiatedCapability] = []
    mutation: list[NegotiatedCapability] = []
    seen: set[CapabilityIdentifier] = set()
    unknown_capability_count = 0
    incompatible_capability_count = 0

    for row in capability_rows:
        if type(row) is not dict:
            raise ContractValidationError("capability must be an object")
        unknown_fields.add_mapping(row, _CAPABILITY_FIELDS)
        try:
            identifier = CapabilityIdentifier(row["id"])
            version_rows = row["versions"]
        except KeyError as error:
            raise ContractValidationError("invalid capability") from error
        if identifier in seen:
            raise ContractValidationError("duplicate capability")
        seen.add(identifier)
        if type(version_rows) is not list:
            raise ContractValidationError("capability versions must be a list")
        if not version_rows:
            raise ContractValidationError("capability versions must not be empty")
        if len(version_rows) > MAX_VERSIONS_PER_CAPABILITY:
            raise ContractValidationError("too many capability versions")

        offered_versions = {
            _parse_version(version_row, unknown_fields) for version_row in version_rows
        }
        definition = _CAPABILITY_DEFINITIONS.get(identifier)
        if definition is None:
            unknown_capability_count += 1
            continue
        common_versions = offered_versions.intersection(definition.versions)
        if not common_versions:
            incompatible_capability_count += 1
            continue
        negotiated = NegotiatedCapability(
            identifier=identifier,
            version=max(common_versions),
            authority=definition.authority,
        )
        if definition.authority is CapabilityAuthority.DISCOVERY:
            discovery.append(negotiated)
        elif definition.authority is CapabilityAuthority.OBSERVATION:
            observation.append(negotiated)
        else:
            mutation.append(negotiated)

    diagnostics: list[ContractDiagnostic] = []
    if unknown_fields.count:
        diagnostics.append(_diagnostic("unknown_fields_ignored", unknown_fields.count))
    if unknown_capability_count:
        diagnostics.append(
            _diagnostic("unknown_capabilities_ignored", unknown_capability_count)
        )
    if incompatible_capability_count:
        diagnostics.append(
            _diagnostic("incompatible_capability_versions", incompatible_capability_count)
        )
    negotiated = (*discovery, *observation, *mutation)
    product_capabilities, unknown_product_count, unsupported_product_count = (
        _parse_product_capabilities(raw, unknown_fields, negotiated, adapter_id)
    )
    if unknown_product_count:
        diagnostics.append(
            _diagnostic("unknown_product_capabilities_ignored", unknown_product_count)
        )
    if unsupported_product_count:
        diagnostics.append(
            _diagnostic("unsupported_product_capabilities", unsupported_product_count)
        )
    if len(diagnostics) > MAX_DIAGNOSTICS:
        raise ContractValidationError("too many diagnostics")

    return NegotiatedProviderContract(
        schema_version=schema_version,
        provider_id=provider_id,
        adapter_id=adapter_id,
        source_instance_id=source_instance_id,
        status=ContractStatus.PARTIAL if diagnostics else ContractStatus.SUPPORTED,
        discovery_capabilities=tuple(discovery),
        observation_capabilities=tuple(observation),
        compatible_mutation_capabilities=tuple(mutation),
        diagnostics=tuple(diagnostics),
        product_capabilities=product_capabilities,
    )


def negotiate_capacity_source_contract(
    declared_source: SourceKey,
    available_sources: tuple[object, ...],
) -> object:
    """Require one exact negotiated capacity row for a declared source.

    Matching uses the complete ``SourceKey``. Provider-family, adapter, source
    instance, or capability-only fallback is intentionally unavailable.
    """
    from .providers import NegotiatedProviderSource

    if not isinstance(declared_source, SourceKey) or type(available_sources) is not tuple:
        raise ContractValidationError("invalid capacity source negotiation")
    if len(available_sources) > MAX_CAPABILITY_DECLARATIONS:
        raise ContractValidationError("too many capacity sources")
    if not all(type(source) is NegotiatedProviderSource for source in available_sources):
        raise ContractValidationError("invalid capacity source negotiation")
    if declared_source.capability_id != "remote_quota_windows":
        raise ContractValidationError("invalid capacity source capability")

    matches = tuple(
        source for source in available_sources if source.source_key == declared_source
    )
    if len(matches) != 1:
        raise ContractValidationError(
            "capacity capability requires exactly one negotiated source"
        )
    match = matches[0]
    if (
        match.source_key.provider_id != declared_source.provider_id
        or match.declared_capability_id
        != CapabilityIdentifier("remote_quota_windows")
        or match.negotiated_capability is None
        or match.negotiated_capability.authority is not CapabilityAuthority.OBSERVATION
        or not match.observation_invocation_allowed
    ):
        raise ContractValidationError("capacity source is not invocation eligible")
    return match
