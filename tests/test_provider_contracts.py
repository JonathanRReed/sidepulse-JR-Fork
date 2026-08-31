from __future__ import annotations

from collections.abc import Iterator

import pytest

from sidepulse.capacity_types import (
    MAX_LANES_PER_OBSERVATION,
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
    ObservationState,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneKey,
    ResetFact,
    ResetState,
    SourceHealthKind,
    SourceKey,
)
from sidepulse.provider_contracts import (
    MAX_CAPABILITY_DECLARATIONS,
    MAX_DIAGNOSTICS,
    MAX_IDENTIFIER_LENGTH,
    MAX_UNKNOWN_FIELDS,
    MAX_VERSIONS_PER_CAPABILITY,
    ActionIdentity,
    AdapterIdentifier,
    CapabilityAuthority,
    CapabilityIdentifier,
    CapacityLaneDescriptor,
    CapacitySourceDescriptor,
    ContractDiagnostic,
    ContractStatus,
    ContractValidationError,
    DiagnosticIdentifier,
    LocalRuntimeSurfaceIdentifier,
    ProductCapability,
    ProductCapabilityBinding,
    ProductCapabilityDeclaration,
    ProductCapabilityInvocation,
    ProviderIdentifier,
    SchemaVersion,
    SourceInstanceIdentifier,
    negotiate_provider_contract,
    product_capability_document,
    provider_contract_document,
)
from sidepulse.provider_facts import ObservationAuthority
from sidepulse.providers import ProviderSourceRegistration


def _version(major: int, minor: int) -> dict[str, int]:
    return {"major": major, "minor": minor}


def _capability(
    identifier: str,
    *versions: tuple[int, int],
    **extra: object,
) -> dict[str, object]:
    return {
        "id": identifier,
        "versions": [_version(major, minor) for major, minor in versions],
        **extra,
    }


def _document(
    *,
    provider_id: str = "codex",
    adapter_id: str = "hooks",
    source_instance_id: str = "source:local-01",
    schema_major: int = 1,
    schema_minor: int = 0,
    capabilities: object | None = None,
    **extra: object,
) -> dict[str, object]:
    return {
        "schema_version": _version(schema_major, schema_minor),
        "provider_id": provider_id,
        "adapter_id": adapter_id,
        "source_instance_id": source_instance_id,
        "capabilities": [] if capabilities is None else capabilities,
        **extra,
    }


def _ids(capabilities) -> tuple[str, ...]:
    return tuple(capability.identifier.value for capability in capabilities)


def _capacity_source_key(
    *,
    capability_id: str = "remote_quota_windows",
) -> SourceKey:
    return SourceKey("codex", "quota", "source:local-01", capability_id)


def _capacity_lane_key(
    *,
    window: str = "session",
    effect: QuotaEffect = QuotaEffect.ALL_WORKLOADS,
) -> QuotaLaneKey:
    return QuotaLaneKey(
        source=_capacity_source_key(),
        opaque_scope="all",
        pool="requests",
        model=None,
        window=window,
        effect=effect,
    )


def _capacity_descriptor(
    *lanes: CapacityLaneDescriptor,
    version: SchemaVersion = SchemaVersion(1, 0),
) -> CapacitySourceDescriptor:
    return CapacitySourceDescriptor(
        provider_id=ProviderIdentifier("codex"),
        adapter_id=AdapterIdentifier("quota"),
        source_instance_id=SourceInstanceIdentifier("source:local-01"),
        capability_id=CapabilityIdentifier("remote_quota_windows"),
        capability_version=version,
        lanes=lanes,
    )


def test_static_registration_builds_the_exact_v1_contract_document() -> None:
    """A widened or non-built-in declaration could bypass the v1 negotiator boundary."""
    registration = ProviderSourceRegistration(
        provider_id=ProviderIdentifier("codex"),
        adapter_id=AdapterIdentifier("hooks"),
        source_instance_id=SourceInstanceIdentifier("global"),
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        capability_versions=(
            (
                CapabilityIdentifier("live_agent_events"),
                (SchemaVersion(1, 0), SchemaVersion(1, 1)),
            ),
            (
                CapabilityIdentifier("actionable_requests"),
                (SchemaVersion(1, 0),),
            ),
        ),
    )

    document = provider_contract_document(registration)

    assert type(document) is dict
    assert document == {
        "schema_version": {"major": 1, "minor": 0},
        "provider_id": "codex",
        "adapter_id": "hooks",
        "source_instance_id": "global",
        "capabilities": [
            {
                "id": "live_agent_events",
                "versions": [
                    {"major": 1, "minor": 0},
                    {"major": 1, "minor": 1},
                ],
            },
            {
                "id": "actionable_requests",
                "versions": [{"major": 1, "minor": 0}],
            },
        ],
        "product_capabilities": [
            {"id": "lifecycle", "supported": False, "binding": None},
            {"id": "questions", "supported": False, "binding": None},
            {"id": "answering", "supported": False, "binding": None},
            {"id": "usage", "supported": False, "binding": None},
            {"id": "costs", "supported": False, "binding": None},
            {"id": "reset_forecasts", "supported": False, "binding": None},
            {"id": "remote_observation", "supported": False, "binding": None},
            {"id": "transcript_fallback", "supported": False, "binding": None},
            {
                "id": "invocation_scoped_monitoring",
                "supported": False,
                "binding": None,
            },
        ],
    }
    assert type(document["schema_version"]) is dict
    assert type(document["capabilities"]) is list
    assert all(type(row) is dict for row in document["capabilities"])


def test_product_capabilities_require_explicit_yes_no_and_exact_binding() -> None:
    declarations = (
        ProductCapabilityDeclaration(
            ProductCapability.LIFECYCLE,
            supported=True,
            binding=ProductCapabilityBinding.low_level(
                "live_agent_events", SchemaVersion(1, 1)
            ),
        ),
        ProductCapabilityDeclaration(
            ProductCapability.ANSWERING,
            supported=False,
        ),
    )

    assert product_capability_document(declarations) == [
        {
            "id": "lifecycle",
            "supported": True,
            "binding": {
                "kind": "low_level",
                "id": "live_agent_events",
                "version": {"major": 1, "minor": 1},
            },
        },
        {"id": "answering", "supported": False, "binding": None},
    ]


def test_product_capability_support_is_not_inferred_from_questions() -> None:
    result = negotiate_provider_contract(
        _document(
            capabilities=[_capability("actionable_requests", (1, 0))],
            product_capabilities=[
                {"id": "questions", "supported": True, "binding": {
                    "kind": "low_level", "id": "actionable_requests",
                    "version": _version(1, 0),
                }},
            ],
        )
    )

    assert result.product_capability(ProductCapability.QUESTIONS).supported is True
    assert result.product_capability(ProductCapability.ANSWERING).supported is False
    assert result.product_capability(ProductCapability.ANSWERING).binding is None


def test_absent_product_capabilities_are_visible_and_inert() -> None:
    result = negotiate_provider_contract(_document())

    assert tuple(declaration.capability for declaration in result.product_capabilities) == (
        ProductCapability.LIFECYCLE,
        ProductCapability.QUESTIONS,
        ProductCapability.ANSWERING,
        ProductCapability.USAGE,
        ProductCapability.COSTS,
        ProductCapability.RESET_FORECASTS,
        ProductCapability.REMOTE_OBSERVATION,
        ProductCapability.TRANSCRIPT_FALLBACK,
        ProductCapability.INVOCATION_SCOPED_MONITORING,
    )
    assert all(not declaration.supported for declaration in result.product_capabilities)
    assert all(declaration.binding is None for declaration in result.product_capabilities)


def test_product_capability_binding_must_be_negotiated_or_named_local_surface() -> None:
    result = negotiate_provider_contract(
        _document(
            product_capabilities=[
                {"id": "usage", "supported": True, "binding": {
                    "kind": "low_level", "id": "transcript_usage",
                    "version": _version(1, 0),
                }},
                {"id": "remote_observation", "supported": True, "binding": {
                    "kind": "local", "id": "local.remote_observation",
                }},
            ]
        )
    )

    assert result.product_capability(ProductCapability.USAGE).supported is False
    assert result.product_capability(ProductCapability.USAGE).binding is None
    assert result.product_capability(ProductCapability.REMOTE_OBSERVATION).supported is True


def test_product_invocation_preserves_exact_source_and_version_identity() -> None:
    result = negotiate_provider_contract(
        _document(
            source_instance_id="opaque:a-b",
            capabilities=[_capability("live_agent_events", (1, 1))],
            product_capabilities=[
                {"id": "lifecycle", "supported": True, "binding": {
                    "kind": "low_level", "id": "live_agent_events",
                    "version": _version(1, 1),
                }},
            ],
        )
    )

    invocation = result.product_invocation_for(ProductCapability.LIFECYCLE)
    assert invocation.provider_id == ProviderIdentifier("codex")
    assert invocation.adapter_id == AdapterIdentifier("hooks")
    assert invocation.source_instance_id == SourceInstanceIdentifier("opaque:a-b")
    assert invocation.capability_id == CapabilityIdentifier("live_agent_events")
    assert invocation.capability_version == SchemaVersion(1, 1)


def test_product_declaration_rejects_implicit_or_ambiguous_support() -> None:
    with pytest.raises(ContractValidationError, match="product support must be a boolean"):
        ProductCapabilityDeclaration(ProductCapability.USAGE, 1)  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="requires a binding"):
        ProductCapabilityDeclaration(ProductCapability.USAGE, True)
    with pytest.raises(ContractValidationError, match="cannot have a binding"):
        ProductCapabilityDeclaration(
            ProductCapability.USAGE,
            False,
            ProductCapabilityBinding.local("local.usage"),
        )
    with pytest.raises(ContractValidationError, match="exactly one surface"):
        ProductCapabilityBinding()


def test_answering_cannot_bind_account_switching_mutation() -> None:
    result = negotiate_provider_contract(
        _document(
            capabilities=[_capability("account_switching", (1, 0))],
            product_capabilities=[
                {"id": "answering", "supported": True, "binding": {
                    "kind": "low_level", "id": "account_switching",
                    "version": _version(1, 0),
                }},
            ],
        )
    )

    assert result.observation_capabilities == ()
    assert result.product_capability(ProductCapability.ANSWERING).supported is False
    assert result.product_capability(ProductCapability.ANSWERING).binding is None
    with pytest.raises(ContractValidationError, match="not supported"):
        result.product_invocation_for("answering")


def test_answering_supports_only_exact_local_answer_surface_and_preserves_identity() -> None:
    result = negotiate_provider_contract(
        _document(
            provider_id="claude",
            source_instance_id="opaque:answerable",
            product_capabilities=[
                {
                    "id": "answering",
                    "supported": True,
                    "binding": {
                        "kind": "local",
                        "id": "local.answer_in_place",
                    },
                }
            ],
        )
    )

    declaration = result.product_capability(ProductCapability.ANSWERING)
    invocation = result.product_invocation_for(ProductCapability.ANSWERING)

    assert declaration.supported is True
    assert declaration.binding is not None
    assert declaration.binding.local_runtime_surface == LocalRuntimeSurfaceIdentifier(
        "local.answer_in_place"
    )
    assert invocation.provider_id == ProviderIdentifier("claude")
    assert invocation.adapter_id == AdapterIdentifier("hooks")
    assert invocation.source_instance_id == SourceInstanceIdentifier("opaque:answerable")
    assert invocation.local_runtime_surface == LocalRuntimeSurfaceIdentifier(
        "local.answer_in_place"
    )
    assert invocation.capability_id is None
    assert invocation.capability_version is None


def test_answering_rejects_non_exact_local_surface_name() -> None:
    result = negotiate_provider_contract(
        _document(
            product_capabilities=[
                {
                    "id": "answering",
                    "supported": True,
                    "binding": {
                        "kind": "local",
                        "id": "local.answer_elsewhere",
                    },
                }
            ],
        )
    )

    assert result.product_capability(ProductCapability.ANSWERING).supported is False
    assert result.product_capability(ProductCapability.ANSWERING).binding is None


@pytest.mark.parametrize(
    ("product_id", "low_level_id"),
    [
        ("lifecycle", "actionable_requests"),
        ("questions", "live_agent_events"),
        ("usage", "account_switching"),
    ],
)
def test_product_capability_bindings_must_use_exact_semantic_low_level_id(
    product_id: str,
    low_level_id: str,
) -> None:
    result = negotiate_provider_contract(
        _document(
            capabilities=[_capability(low_level_id, (1, 0))],
            product_capabilities=[
                {
                    "id": product_id,
                    "supported": True,
                    "binding": {
                        "kind": "low_level",
                        "id": low_level_id,
                        "version": _version(1, 0),
                    },
                }
            ],
        )
    )

    assert result.product_capability(product_id).supported is False
    assert result.product_capability(product_id).binding is None


def test_transcript_fallback_requires_transcript_adapter_and_usage_capability() -> None:
    unsupported_adapter = negotiate_provider_contract(
        _document(
            adapter_id="hooks",
            capabilities=[_capability("transcript_usage", (1, 0))],
            product_capabilities=[
                {
                    "id": "transcript_fallback",
                    "supported": True,
                    "binding": {
                        "kind": "low_level",
                        "id": "transcript_usage",
                        "version": _version(1, 0),
                    },
                }
            ],
        )
    )
    supported_adapter = negotiate_provider_contract(
        _document(
            adapter_id="transcripts",
            capabilities=[_capability("transcript_usage", (1, 0))],
            product_capabilities=[
                {
                    "id": "transcript_fallback",
                    "supported": True,
                    "binding": {
                        "kind": "low_level",
                        "id": "transcript_usage",
                        "version": _version(1, 0),
                    },
                }
            ],
        )
    )

    assert unsupported_adapter.product_capability("transcript_fallback").supported is False
    assert supported_adapter.product_capability("transcript_fallback").supported is True


def test_transcript_fallback_cannot_bypass_usage_source_with_local_binding() -> None:
    result = negotiate_provider_contract(
        _document(
            adapter_id="transcripts",
            product_capabilities=[
                {
                    "id": "transcript_fallback",
                    "supported": True,
                    "binding": {"kind": "local", "id": "local.transcript_fallback"},
                }
            ],
        )
    )

    assert result.product_capability("transcript_fallback").supported is False


def test_invocation_monitoring_requires_an_explicit_local_surface() -> None:
    result = negotiate_provider_contract(
        _document(
            capabilities=[_capability("live_agent_events", (1, 0))],
            product_capabilities=[
                {
                    "id": "invocation_scoped_monitoring",
                    "supported": True,
                    "binding": {
                        "kind": "local",
                        "id": "local.invocation_scoped_monitoring",
                    },
                }
            ],
        )
    )

    declaration = result.product_capability("invocation_scoped_monitoring")
    assert declaration.supported is True
    invocation = result.product_invocation_for("invocation_scoped_monitoring")
    assert invocation.capability_id is None
    assert invocation.local_runtime_surface is not None


def test_product_invocation_cannot_represent_mutation_authority() -> None:
    with pytest.raises(ContractValidationError, match="not allowed"):
        ProductCapabilityInvocation(
            product_capability=ProductCapability.ANSWERING,
            provider_id=ProviderIdentifier("codex"),
            adapter_id=AdapterIdentifier("hooks"),
            source_instance_id=SourceInstanceIdentifier("source:local-01"),
            capability_id=CapabilityIdentifier("account_switching"),
            capability_version=SchemaVersion(1, 0),
        )


def test_each_capability_negotiates_its_highest_exact_version() -> None:
    """Using one contract-wide version would select an unsupported capability."""
    result = negotiate_provider_contract(
        _document(
            schema_minor=42,
            capabilities=[
                _capability("live_agent_events", (1, 0), (1, 1), (1, 2)),
                _capability("transcript_usage", (1, 0)),
                _capability("account_switching", (1, 0)),
            ],
        )
    )

    assert result.status is ContractStatus.SUPPORTED
    assert result.schema_version == SchemaVersion(1, 42)
    assert _ids(result.observation_capabilities) == ("live_agent_events",)
    assert result.observation_capabilities[0].version == SchemaVersion(1, 1)
    assert _ids(result.discovery_capabilities) == ("transcript_usage",)
    assert _ids(result.compatible_mutation_capabilities) == ("account_switching",)
    assert all(
        capability.authority is not CapabilityAuthority.MUTATION
        for capability in (*result.discovery_capabilities, *result.observation_capabilities)
    )


def test_declared_mutation_never_grants_observation_invocation() -> None:
    """Treating any compatible flag as read authority would invoke mutation-only sources."""
    result = negotiate_provider_contract(
        _document(capabilities=[_capability("account_switching", (1, 0))])
    )

    assert _ids(result.compatible_mutation_capabilities) == ("account_switching",)
    assert result.discovery_capabilities == ()
    assert result.observation_capabilities == ()
    assert result.observation_invocation_allowed is False
    with pytest.raises(ContractValidationError, match="capability is not negotiated"):
        result.action_identity_for("account_switching")


class _ExplodingCapabilities:
    def __iter__(self) -> Iterator[object]:
        raise AssertionError("unsupported-major capabilities were traversed")


class _ExplodingList(list):
    def __iter__(self) -> Iterator[object]:
        raise AssertionError("custom collection was traversed")


def test_unsupported_schema_major_stays_visible_and_invokes_nothing() -> None:
    """Parsing an unknown major could accidentally activate incompatible behavior."""
    result = negotiate_provider_contract(
        _document(schema_major=2, capabilities=_ExplodingCapabilities())
    )

    assert result.status is ContractStatus.UNSUPPORTED_SCHEMA_MAJOR
    assert result.provider_id == ProviderIdentifier("codex")
    assert result.adapter_id == AdapterIdentifier("hooks")
    assert result.source_instance_id == SourceInstanceIdentifier("source:local-01")
    assert result.discovery_capabilities == ()
    assert result.observation_capabilities == ()
    assert result.compatible_mutation_capabilities == ()
    assert result.observation_invocation_allowed is False
    assert tuple(diagnostic.identifier.value for diagnostic in result.diagnostics) == (
        "unsupported_schema_major",
    )


@pytest.mark.parametrize(
    ("overrides", "expected_status"),
    [
        ({"provider_id": "future-provider"}, ContractStatus.UNSUPPORTED_PROVIDER),
        ({"adapter_id": "future-adapter"}, ContractStatus.UNSUPPORTED_ADAPTER),
    ],
)
def test_only_static_first_party_provider_and_adapter_pairs_are_eligible(
    overrides: dict[str, str],
    expected_status: ContractStatus,
) -> None:
    """Accepting a valid-looking unknown identifier would create a plugin loader."""
    result = negotiate_provider_contract(
        _document(
            **overrides,
            capabilities=_ExplodingCapabilities(),
        )
    )

    assert result.status is expected_status
    assert result.observation_capabilities == ()
    assert result.observation_invocation_allowed is False


class _Poison:
    def __repr__(self) -> str:
        raise AssertionError("unknown value was reflected")

    def __str__(self) -> str:
        raise AssertionError("unknown value was coerced")

    def __iter__(self) -> Iterator[object]:
        raise AssertionError("unknown value was traversed")


def test_same_major_unknown_fields_and_capabilities_are_inert_and_counted() -> None:
    """Additive data must not be interpreted, retained, or copied into diagnostics."""
    result = negotiate_provider_contract(
        _document(
            schema_minor=999,
            capabilities=[
                _capability("live_agent_events", (1, 1), future_capability_data=_Poison()),
                _capability("future_read_surface", (77, 4), future_payload=_Poison()),
            ],
            future_contract_data=_Poison(),
        )
    )

    assert result.status is ContractStatus.PARTIAL
    assert _ids(result.observation_capabilities) == ("live_agent_events",)
    assert result.diagnostics[0].identifier == DiagnosticIdentifier("unknown_fields_ignored")
    assert result.diagnostics[0].count == 3
    assert result.diagnostics[1].identifier == DiagnosticIdentifier("unknown_capabilities_ignored")
    assert result.diagnostics[1].count == 1
    assert len(result.diagnostics) <= MAX_DIAGNOSTICS


def test_absent_capability_remains_absent_without_inference() -> None:
    """Defaulting related capabilities would fabricate unsupported provider facts."""
    result = negotiate_provider_contract(
        _document(capabilities=[_capability("reset_metadata", (1, 0))])
    )

    assert _ids(result.observation_capabilities) == ("reset_metadata",)
    assert "remote_quota_windows" not in _ids(result.observation_capabilities)
    assert result.discovery_capabilities == ()
    assert result.compatible_mutation_capabilities == ()


def test_known_capability_with_no_exact_version_is_inert_and_partial() -> None:
    """Matching by major or choosing the nearest version would violate exact negotiation."""
    result = negotiate_provider_contract(
        _document(capabilities=[_capability("live_agent_events", (1, 2), (2, 0))])
    )

    assert result.status is ContractStatus.PARTIAL
    assert result.observation_capabilities == ()
    assert result.observation_invocation_allowed is False
    assert tuple(diagnostic.identifier.value for diagnostic in result.diagnostics) == (
        "incompatible_capability_versions",
    )
    assert result.diagnostics[0].count == 1


def test_action_identity_includes_the_opaque_source_instance() -> None:
    """Dropping or splitting source identity could route an action to a sibling source."""
    first = negotiate_provider_contract(
        _document(
            source_instance_id="opaque:a-b",
            capabilities=[_capability("direct_navigation", (1, 0))],
        )
    )
    second = negotiate_provider_contract(
        _document(
            source_instance_id="opaque-a:b",
            capabilities=[_capability("direct_navigation", (1, 0))],
        )
    )

    first_identity = first.action_identity_for("direct_navigation")
    second_identity = second.action_identity_for("direct_navigation")

    assert first_identity == ActionIdentity(
        provider_id=ProviderIdentifier("codex"),
        adapter_id=AdapterIdentifier("hooks"),
        source_instance_id=SourceInstanceIdentifier("opaque:a-b"),
        capability_id=CapabilityIdentifier("direct_navigation"),
    )
    assert first_identity != second_identity
    assert first.source_instance_id.value == "opaque:a-b"
    assert second.source_instance_id.value == "opaque-a:b"


def test_action_identity_rejects_capabilities_not_negotiated_for_that_source() -> None:
    """Constructing a target for an absent capability would bypass negotiation."""
    result = negotiate_provider_contract(
        _document(capabilities=[_capability("live_agent_events", (1, 1))])
    )

    with pytest.raises(ContractValidationError, match="capability is not negotiated"):
        result.action_identity_for("direct_navigation")


@pytest.mark.parametrize(
    ("identifier_type", "value"),
    [
        (ProviderIdentifier, ""),
        (ProviderIdentifier, "UPPERCASE"),
        (AdapterIdentifier, "has/slash"),
        (CapabilityIdentifier, " leading"),
        (DiagnosticIdentifier, "contains space"),
        (SourceInstanceIdentifier, "line\nbreak"),
        (SourceInstanceIdentifier, "/Users/private/path"),
        (ProviderIdentifier, "x" * (MAX_IDENTIFIER_LENGTH + 1)),
    ],
)
def test_identifier_types_reject_unbounded_or_ambiguous_values(
    identifier_type,
    value: str,
) -> None:
    """Loose strings could leak paths, controls, or oversized attacker labels."""
    with pytest.raises(ContractValidationError, match="invalid identifier"):
        identifier_type(value)


@pytest.mark.parametrize(
    "field_value",
    [None, True, 1, b"codex", [], {}],
)
def test_contract_identifier_fields_require_strings(field_value: object) -> None:
    """Coercing non-strings could collapse unrelated source identities."""
    with pytest.raises(ContractValidationError, match="invalid identifier"):
        negotiate_provider_contract(_document(source_instance_id=field_value))


@pytest.mark.parametrize(
    "bad_version",
    [
        {"major": True, "minor": 0},
        {"major": 1, "minor": False},
        {"major": 0, "minor": 0},
        {"major": 1, "minor": -1},
        {"major": 65_536, "minor": 0},
        {"major": 1, "minor": 65_536},
        {"major": "1", "minor": 0},
    ],
)
def test_versions_reject_booleans_nonintegers_and_out_of_range_values(
    bad_version: dict[str, object],
) -> None:
    """Python booleans and unbounded integers must not masquerade as versions."""
    document = _document()
    document["schema_version"] = bad_version

    with pytest.raises(ContractValidationError, match="invalid version"):
        negotiate_provider_contract(document)


@pytest.mark.parametrize(
    "capabilities",
    [None, {}, (), "live_agent_events", b"live_agent_events"],
)
def test_capability_collection_requires_a_json_array(capabilities: object) -> None:
    """Accepting arbitrary iterables would permit surprising or unbounded traversal."""
    document = _document()
    document["capabilities"] = capabilities

    with pytest.raises(ContractValidationError, match="capabilities must be a list"):
        negotiate_provider_contract(document)


def test_same_major_rejects_custom_collection_types_without_traversing_them() -> None:
    """A list subclass could execute provider code during otherwise pure validation."""
    document = _document()
    document["capabilities"] = _ExplodingList()

    with pytest.raises(ContractValidationError, match="capabilities must be a list"):
        negotiate_provider_contract(document)


def test_capability_and_version_collection_bounds_fail_closed() -> None:
    """Unlimited declaration fan-out would defeat the bounded pure foundation."""
    too_many_capabilities = [
        _capability(f"future_capability_{index}", (1, 0))
        for index in range(MAX_CAPABILITY_DECLARATIONS + 1)
    ]
    too_many_versions = [
        _version(1, index) for index in range(MAX_VERSIONS_PER_CAPABILITY + 1)
    ]

    with pytest.raises(ContractValidationError, match="too many capabilities"):
        negotiate_provider_contract(_document(capabilities=too_many_capabilities))

    with pytest.raises(ContractValidationError, match="too many capability versions"):
        negotiate_provider_contract(
            _document(
                capabilities=[
                    {"id": "live_agent_events", "versions": too_many_versions},
                ]
            )
        )


def test_unknown_field_budget_includes_nested_capability_and_version_fields() -> None:
    """Counting only top-level drift would leave nested additive data unbounded."""
    capability = _capability("live_agent_events", (1, 1))
    version = capability["versions"][0]
    for index in range(MAX_UNKNOWN_FIELDS):
        capability[f"future_{index}"] = _Poison()
    version["one_too_many"] = _Poison()

    with pytest.raises(ContractValidationError, match="too many unknown fields"):
        negotiate_provider_contract(_document(capabilities=[capability]))


def test_duplicate_capability_identifier_fails_closed() -> None:
    """Merging duplicates could make input order change negotiated authority."""
    with pytest.raises(ContractValidationError, match="duplicate capability"):
        negotiate_provider_contract(
            _document(
                capabilities=[
                    _capability("direct_navigation", (1, 0)),
                    _capability("direct_navigation", (1, 0)),
                ]
            )
        )


def test_diagnostic_identifiers_and_counts_remain_static_and_bounded() -> None:
    """Diagnostics must not echo arbitrary capability identifiers or create one row each."""
    capabilities = [
        _capability(f"future_capability_{index}", (1, 0))
        for index in range(MAX_CAPABILITY_DECLARATIONS)
    ]
    result = negotiate_provider_contract(_document(capabilities=capabilities))

    assert result.status is ContractStatus.PARTIAL
    assert result.diagnostics == (
        result.diagnostics[0],
    )
    assert result.diagnostics[0].identifier == DiagnosticIdentifier(
        "unknown_capabilities_ignored"
    )
    assert result.diagnostics[0].count == MAX_CAPABILITY_DECLARATIONS
    assert len(result.diagnostics) <= MAX_DIAGNOSTICS
    assert all(
        diagnostic.identifier.value != capability["id"]
        for diagnostic in result.diagnostics
        for capability in capabilities
    )


def test_public_records_cannot_bypass_bounded_identifier_and_count_types() -> None:
    """Dataclass annotations alone would allow unvalidated action targets and counts."""
    with pytest.raises(ContractValidationError, match="invalid action identity"):
        ActionIdentity(
            provider_id="codex",
            adapter_id=AdapterIdentifier("hooks"),
            source_instance_id=SourceInstanceIdentifier("source:local-01"),
            capability_id=CapabilityIdentifier("direct_navigation"),
        )

    with pytest.raises(ContractValidationError, match="invalid diagnostic count"):
        ContractDiagnostic(
            DiagnosticIdentifier("unknown_capabilities_ignored"),
            MAX_CAPABILITY_DECLARATIONS + 1,
        )


def test_capacity_descriptor_supplies_semantic_name_and_horizon() -> None:
    """Provider labels or reset duration must not author window semantics."""
    key = _capacity_lane_key(window="provider-window-300")
    descriptor = _capacity_descriptor(
        CapacityLaneDescriptor(
            key=key,
            semantic_name="Session window",
            horizon=QuotaHorizon.SHORT,
            bindable=True,
        )
    )
    health = CapacitySourceHealth(
        source=descriptor.source,
        kind=SourceHealthKind.HEALTHY,
        observed_at=1_800_000_000.0,
        last_attempt_at=1_800_000_000.0,
        retry_at=None,
        reason_code=None,
        has_last_known_good=False,
    )

    observation = descriptor.build_observation(
        key=key,
        value=CapacityValue(
            CapacityUnit.PERCENT_REMAINING,
            25.0,
            ObservationState.OBSERVED,
        ),
        reset=ResetFact(
            state=ResetState.FUTURE,
            reset_epoch=1_800_003_600.0,
            window_minutes=10_080.0,
            observed_at=1_800_000_000.0,
        ),
        observed_at=1_800_000_000.0,
        source_health=health,
        account_discriminator=None,
    )

    assert observation.semantic_name == "Session window"
    assert observation.horizon is QuotaHorizon.SHORT
    assert observation.reset.window_minutes == 10_080.0
    assert observation.key.window == "provider-window-300"


def test_unknown_effect_cannot_be_declared_bindable() -> None:
    """Unknown scope authority must never become eligible through a descriptor flag."""
    unknown_key = _capacity_lane_key(effect=QuotaEffect.UNKNOWN)

    with pytest.raises(ContractValidationError, match="unknown quota effect"):
        CapacityLaneDescriptor(
            key=unknown_key,
            semantic_name="Other window",
            horizon=QuotaHorizon.OTHER,
            bindable=True,
        )

    descriptor = CapacityLaneDescriptor(
        key=unknown_key,
        semantic_name="Other window",
        horizon=QuotaHorizon.OTHER,
        bindable=False,
    )
    assert descriptor.bindable is False


def test_capacity_source_descriptor_uses_exact_contract_identity_and_capability_version() -> None:
    """Dropping capability or source-instance identity would merge sibling quota sources."""
    lane = CapacityLaneDescriptor(
        key=_capacity_lane_key(),
        semantic_name="Session window",
        horizon=QuotaHorizon.SHORT,
        bindable=True,
    )
    descriptor = _capacity_descriptor(lane)

    assert descriptor.source == _capacity_source_key()
    assert descriptor.capability_version == SchemaVersion(1, 0)
    assert descriptor.lanes == (lane,)


class _ExplodingCapacityLanes:
    def __iter__(self) -> Iterator[object]:
        raise AssertionError("unsupported capacity lanes were traversed")


def test_unsupported_capacity_capability_major_fails_before_lane_traversal() -> None:
    """An unsupported major must stay inert without touching adapter-supplied lane data."""
    with pytest.raises(ContractValidationError, match="unsupported capacity capability version"):
        CapacitySourceDescriptor(
            provider_id=ProviderIdentifier("codex"),
            adapter_id=AdapterIdentifier("quota"),
            source_instance_id=SourceInstanceIdentifier("source:local-01"),
            capability_id=CapabilityIdentifier("remote_quota_windows"),
            capability_version=SchemaVersion(2, 0),
            lanes=_ExplodingCapacityLanes(),  # type: ignore[arg-type]
        )


def test_capacity_source_descriptor_caps_and_deduplicates_lane_keys() -> None:
    """Unbounded or duplicate declarations could make adapter order author truth."""
    duplicate = CapacityLaneDescriptor(
        key=_capacity_lane_key(),
        semantic_name="Session window",
        horizon=QuotaHorizon.SHORT,
        bindable=True,
    )
    with pytest.raises(ContractValidationError, match="duplicate capacity lane"):
        _capacity_descriptor(duplicate, duplicate)

    too_many = tuple(
        CapacityLaneDescriptor(
            key=_capacity_lane_key(window=f"window-{index}"),
            semantic_name=f"Window {index}",
            horizon=QuotaHorizon.OTHER,
            bindable=True,
        )
        for index in range(MAX_LANES_PER_OBSERVATION + 1)
    )
    with pytest.raises(ContractValidationError, match="too many capacity lanes"):
        _capacity_descriptor(*too_many)


def test_capacity_descriptor_rejects_lane_from_another_exact_source() -> None:
    """A lane from a sibling source must not be stamped with this source's semantics."""
    other_key = QuotaLaneKey(
        source=SourceKey("codex", "quota", "source:other", "remote_quota_windows"),
        opaque_scope="all",
        pool="requests",
        model=None,
        window="session",
        effect=QuotaEffect.ALL_WORKLOADS,
    )

    with pytest.raises(ContractValidationError, match="capacity lane source"):
        _capacity_descriptor(
            CapacityLaneDescriptor(
                key=other_key,
                semantic_name="Session window",
                horizon=QuotaHorizon.SHORT,
                bindable=True,
            )
        )


def test_capacity_descriptor_keeps_distinct_feature_scopes() -> None:
    """A descriptor must preserve each declared feature's independent lane identity."""
    fable_key = QuotaLaneKey(
        source=_capacity_source_key(),
        opaque_scope="fable",
        pool="requests",
        model=None,
        window="weekly",
        effect=QuotaEffect.FEATURE,
    )
    research_key = QuotaLaneKey(
        source=_capacity_source_key(),
        opaque_scope="deep-research",
        pool="requests",
        model=None,
        window="weekly",
        effect=QuotaEffect.FEATURE,
    )

    descriptor = _capacity_descriptor(
        CapacityLaneDescriptor(
            key=fable_key,
            semantic_name="Fable-only weekly",
            horizon=QuotaHorizon.LONG,
            bindable=True,
        ),
        CapacityLaneDescriptor(
            key=research_key,
            semantic_name="Weekly window",
            horizon=QuotaHorizon.LONG,
            bindable=True,
        ),
    )

    assert tuple(lane.key.opaque_scope for lane in descriptor.lanes) == (
        "fable",
        "deep-research",
    )


def test_capacity_descriptor_refuses_undeclared_lane_observation() -> None:
    """A source must not inject arbitrary lane labels through a declared descriptor."""
    declared = CapacityLaneDescriptor(
        key=_capacity_lane_key(),
        semantic_name="Session window",
        horizon=QuotaHorizon.SHORT,
        bindable=True,
    )
    descriptor = _capacity_descriptor(declared)
    undeclared = _capacity_lane_key(window="weekly")

    with pytest.raises(ContractValidationError, match="capacity lane is not declared"):
        descriptor.build_observation(
            key=undeclared,
            value=CapacityValue(
                CapacityUnit.PERCENT_REMAINING,
                25.0,
                ObservationState.OBSERVED,
            ),
            reset=ResetFact(
                state=ResetState.UNKNOWN,
                reset_epoch=None,
                window_minutes=None,
                observed_at=1_800_000_000.0,
            ),
            observed_at=1_800_000_000.0,
            source_health=CapacitySourceHealth(
                source=descriptor.source,
                kind=SourceHealthKind.HEALTHY,
                observed_at=1_800_000_000.0,
                last_attempt_at=None,
                retry_at=None,
                reason_code=None,
                has_last_known_good=False,
            ),
            account_discriminator=None,
        )
