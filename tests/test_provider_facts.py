from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError
from math import inf, nan

import pytest

from sidepulse.capacity_types import (
    CapacityValidationError,
    QuotaEffect,
)
from sidepulse.capacity_types import (
    QuotaLaneKey as CapacityQuotaLaneKey,
)
from sidepulse.capacity_types import (
    SourceKey as CapacitySourceKey,
)
from sidepulse.provider_contracts import DiagnosticIdentifier
from sidepulse.provider_facts import (
    MAX_DIAGNOSTICS_PER_BATCH,
    MAX_REQUEST_FACTS_PER_BATCH,
    MAX_WORK_FACTS_PER_BATCH,
    EventToken,
    NextActor,
    ObservationAuthority,
    ProviderFactBatch,
    ProviderFactDiagnostic,
    ProviderFactValidationError,
    ProviderQuotaWindow,
    ProviderRequestFact,
    ProviderRequestState,
    ProviderWatermark,
    ProviderWorkFact,
    QuotaLaneKey,
    RequestIdentifier,
    RequestKey,
    RequestKind,
    SourceFreshness,
    SourceHealth,
    SourceKey,
    WatermarkBasis,
    WatermarkOrder,
    WorkIdentifier,
    WorkKey,
    WorkLifecycle,
    compare_watermarks,
    request_key_from_payload,
    request_key_to_payload,
    work_key_from_payload,
    work_key_to_payload,
)


def _source(
    *,
    provider: str = "codex",
    adapter: str = "hooks",
    instance: str = "local:01",
    capability: str = "live_agent_events",
) -> SourceKey:
    return SourceKey(provider, adapter, instance, capability)


def _work_key(
    value: str = "work:01",
    *,
    source: SourceKey | None = None,
) -> WorkKey:
    return WorkKey(source or _source(), WorkIdentifier(value))


def _request_key(
    value: str = "request:01",
    *,
    work_key: WorkKey | None = None,
) -> RequestKey:
    return RequestKey(work_key or _work_key(), RequestIdentifier(value))


def _watermark(
    *,
    source: SourceKey | None = None,
    basis: WatermarkBasis = WatermarkBasis.PROVIDER_EVENT_ID,
    epoch: float = 1_800_000_000.0,
    token: str = "event:01",
    sequence: int | None = None,
    rank: int = 10,
) -> ProviderWatermark:
    return ProviderWatermark(
        source_key=source or _source(),
        basis=basis,
        occurred_at_epoch=epoch,
        event_token=EventToken(token),
        sequence=sequence,
        tie_break_rank=rank,
    )


def _work_fact(
    value: str = "work:01",
    *,
    source: SourceKey | None = None,
    parent_key: WorkKey | None = None,
) -> ProviderWorkFact:
    actual_source = source or _source()
    key = _work_key(value, source=actual_source)
    return ProviderWorkFact(
        key=key,
        lifecycle=WorkLifecycle.ACTIVE,
        watermark=_watermark(source=actual_source, token=f"event:{value}"),
        safe_label=f"Codex {value}",
        parent_key=parent_key,
        next_actor=NextActor.PROVIDER,
    )


def _request_fact(
    value: str = "request:01",
    *,
    work_key: WorkKey | None = None,
) -> ProviderRequestFact:
    actual_work = work_key or _work_key()
    return ProviderRequestFact(
        key=_request_key(value, work_key=actual_work),
        state=ProviderRequestState.LIVE,
        request_kind=RequestKind.PERMISSION,
        next_actor=NextActor.USER,
        watermark=_watermark(source=actual_work.source_key, token=f"event:{value}"),
    )


def _diagnostic(value: str = "partial_observation") -> ProviderFactDiagnostic:
    return ProviderFactDiagnostic(DiagnosticIdentifier(value), 1)


def _batch(
    *,
    source: SourceKey | None = None,
    work_facts: tuple[ProviderWorkFact, ...] = (),
    request_facts: tuple[ProviderRequestFact, ...] = (),
    diagnostics: tuple[ProviderFactDiagnostic, ...] = (),
) -> ProviderFactBatch:
    actual_source = source or _source()
    return ProviderFactBatch(
        source_key=actual_source,
        observation_authority=ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        source_health=SourceHealth.HEALTHY,
        source_freshness=SourceFreshness.FRESH,
        observed_at_epoch=1_800_000_001.0,
        watermark=_watermark(source=actual_source),
        work_facts=work_facts,
        request_facts=request_facts,
        diagnostics=diagnostics,
    )


def test_source_work_request_and_quota_keys_are_source_scoped_and_orderable() -> None:
    """Dropping source components would merge sibling provider work and quota lanes."""
    assert SourceKey is CapacitySourceKey
    assert QuotaLaneKey is CapacityQuotaLaneKey

    first_source = _source(instance="local:01")
    second_source = _source(instance="local:02")
    first_work = _work_key(source=first_source)
    second_work = _work_key(source=second_source)
    first_request = _request_key(work_key=first_work)
    second_request = _request_key(work_key=second_work)
    first_lane = QuotaLaneKey(
        first_source,
        "all",
        "requests",
        None,
        "session",
        QuotaEffect.ALL_WORKLOADS,
    )
    second_lane = QuotaLaneKey(
        second_source,
        "all",
        "requests",
        None,
        "session",
        QuotaEffect.ALL_WORKLOADS,
    )

    assert first_work != second_work
    assert first_request != second_request
    assert sorted((second_work, first_work)) == [first_work, second_work]
    assert sorted((second_request, first_request)) == [first_request, second_request]
    assert sorted((second_lane, first_lane)) == [first_lane, second_lane]
    assert not hasattr(first_lane, "source_key")
    with pytest.raises(FrozenInstanceError):
        first_work.work_id = WorkIdentifier("changed")  # type: ignore[misc]


def test_work_and_request_key_v1_payloads_are_exact_and_round_trip() -> None:
    """A lossy or additive codec would make persisted identities ambiguous."""
    work_key = _work_key("work:a:b")
    request_key = _request_key("request:x:y", work_key=work_key)
    work_payload = {
        "version": {"major": 1, "minor": 0},
        "provider_id": "codex",
        "adapter_id": "hooks",
        "source_instance_id": "local:01",
        "capability_id": "live_agent_events",
        "work_id": "work:a:b",
    }
    request_payload = {
        **work_payload,
        "request_id": "request:x:y",
    }

    assert work_key_to_payload(work_key) == work_payload
    assert request_key_to_payload(request_key) == request_payload
    assert work_key_from_payload(work_payload) == work_key
    assert request_key_from_payload(request_payload) == request_key


class _ExplosiveMapping(Mapping[object, object]):
    def __getitem__(self, key: object) -> object:
        raise AssertionError("mapping was executed")

    def __iter__(self) -> Iterator[object]:
        raise AssertionError("mapping was iterated")

    def __len__(self) -> int:
        raise AssertionError("mapping length was read")


class _ExplosiveDict(dict[object, object]):
    def __iter__(self) -> Iterator[object]:
        raise AssertionError("dict subclass was iterated")

    def keys(self) -> object:
        raise AssertionError("dict subclass keys were read")

    def __getitem__(self, key: object) -> object:
        raise AssertionError("dict subclass value was read")


def test_key_decoders_reject_extra_missing_unknown_and_executable_mappings() -> None:
    """Permissive mappings could execute code or smuggle unversioned identity fields."""
    valid = work_key_to_payload(_work_key())
    missing = dict(valid)
    missing.pop("work_id")
    extra = {**valid, "display_name": "private project"}
    unknown = {**valid, "version": {"major": 2, "minor": 0}}
    version_extra = {**valid, "version": {"major": 1, "minor": 0, "patch": 1}}

    for payload in (
        missing,
        extra,
        unknown,
        version_extra,
        _ExplosiveMapping(),
        _ExplosiveDict(valid),
        [valid],
        None,
    ):
        assert work_key_from_payload(payload) is None

    request_payload = request_key_to_payload(_request_key())
    assert request_key_from_payload({**request_payload, "extra": 1}) is None
    assert request_key_from_payload(_ExplosiveDict(request_payload)) is None


def test_opaque_components_cannot_collide_through_delimiters() -> None:
    """Concatenating opaque components would let delimiter placement alias two keys."""
    first = WorkKey(
        _source(instance="alpha:beta"),
        WorkIdentifier("gamma"),
    )
    second = WorkKey(
        _source(instance="alpha"),
        WorkIdentifier("beta:gamma"),
    )
    first_request = RequestKey(first, RequestIdentifier("request:a:b"))
    second_request = RequestKey(second, RequestIdentifier("request:a:b"))

    assert first != second
    assert work_key_to_payload(first) != work_key_to_payload(second)
    assert work_key_from_payload(work_key_to_payload(first)) == first
    assert work_key_from_payload(work_key_to_payload(second)) == second
    assert request_key_to_payload(first_request) != request_key_to_payload(second_request)


@pytest.mark.parametrize(
    "component",
    [
        "Agent Display Name",
        "/Users/private/project",
        "person@example.com",
        "Bearer secret-token",
        "work:Bearer_secret",
        "request:api_key:private",
        "event:authorization:private",
        "finish this prompt?",
        "line\nbreak",
        "x" * 65,
        "",
    ],
)
def test_display_labels_paths_and_account_text_are_never_key_inputs(component: str) -> None:
    """Private or display text in an identifier would create durable sensitive linkage."""
    for wrapper in (WorkIdentifier, RequestIdentifier, EventToken):
        with pytest.raises(ProviderFactValidationError, match="invalid opaque identifier"):
            wrapper(component)


def test_opaque_identifiers_enforce_exact_64_character_bound() -> None:
    """An off-by-one identifier cap would admit unbounded provider-controlled identity."""
    assert WorkIdentifier("a" * 64).value == "a" * 64
    with pytest.raises(ProviderFactValidationError, match="invalid opaque identifier"):
        WorkIdentifier("a" * 65)


@pytest.mark.parametrize(
    "component",
    ["work:my_token_123", "event:customer.account.01", "work:secret-agent"],
)
def test_benign_opaque_components_are_not_interpreted_as_provider_copy(
    component: str,
) -> None:
    """Semantic guesses about opaque IDs would silently drop legitimate provider facts."""
    assert WorkIdentifier(component).value == component


def test_provider_sequence_orders_before_time_fallback_for_one_source() -> None:
    """A later receipt clock must not override an authoritative provider sequence."""
    earlier_sequence = _watermark(
        basis=WatermarkBasis.PROVIDER_SEQUENCE,
        sequence=10,
        epoch=1_900_000_000.0,
    )
    later_sequence = _watermark(
        basis=WatermarkBasis.PROVIDER_SEQUENCE,
        sequence=11,
        epoch=1_700_000_000.0,
    )
    time_fallback = _watermark(
        basis=WatermarkBasis.PROVIDER_EVENT_ID,
        sequence=None,
        epoch=1_900_000_001.0,
    )

    assert compare_watermarks(earlier_sequence, later_sequence) is WatermarkOrder.OLDER
    assert compare_watermarks(later_sequence, earlier_sequence) is WatermarkOrder.NEWER
    assert compare_watermarks(earlier_sequence, time_fallback) is WatermarkOrder.OLDER


def test_equal_time_uses_adapter_rank_then_opaque_event_token_deterministically() -> None:
    """Equal provider times need a stable total order independent of tuple order."""
    low_rank = _watermark(rank=1, token="event:z")
    high_rank = _watermark(rank=2, token="event:a")
    low_token = _watermark(rank=2, token="event:a")
    high_token = _watermark(rank=2, token="event:b")

    assert compare_watermarks(low_rank, high_rank) is WatermarkOrder.OLDER
    assert compare_watermarks(high_rank, low_rank) is WatermarkOrder.NEWER
    assert compare_watermarks(low_token, high_token) is WatermarkOrder.OLDER
    assert compare_watermarks(high_token, low_token) is WatermarkOrder.NEWER
    assert compare_watermarks(high_token, high_token) is WatermarkOrder.EQUAL


def test_watermarks_from_different_sources_are_not_comparable() -> None:
    """Cross-source ordering would let one provider advance or suppress another source."""
    with pytest.raises(ValueError, match="watermarks belong to different sources"):
        compare_watermarks(
            _watermark(source=_source(instance="local:01")),
            _watermark(source=_source(instance="local:02")),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"epoch": -0.1},
        {"epoch": nan},
        {"epoch": inf},
        {"epoch": True},
        {"rank": -1},
        {"rank": 256},
        {"rank": True},
        {"basis": WatermarkBasis.PROVIDER_SEQUENCE, "sequence": None},
        {"basis": WatermarkBasis.PROVIDER_EVENT_ID, "sequence": 1},
        {"basis": WatermarkBasis.PROVIDER_SEQUENCE, "sequence": -1},
        {"basis": WatermarkBasis.PROVIDER_SEQUENCE, "sequence": True},
    ],
)
def test_watermark_rejects_invalid_numeric_and_basis_combinations(
    kwargs: dict[str, object],
) -> None:
    """Malformed clocks and ambiguous sequences must not enter reducer ordering."""
    with pytest.raises(ProviderFactValidationError, match="invalid provider watermark"):
        _watermark(**kwargs)  # type: ignore[arg-type]


def test_fact_batch_rejects_cross_source_children_duplicates_and_oversize() -> None:
    """A batch must not merge sibling sources, duplicate keys, or exceed hard caps."""
    source = _source()
    sibling = _source(instance="local:02")
    work = _work_fact(source=source)
    request = _request_fact(work_key=work.key)

    assert _batch(
        source=source,
        work_facts=tuple(_work_fact(f"work:{index}") for index in range(MAX_WORK_FACTS_PER_BATCH)),
        request_facts=tuple(
            _request_fact(f"request:{index}") for index in range(MAX_REQUEST_FACTS_PER_BATCH)
        ),
        diagnostics=tuple(
            _diagnostic(f"partial_{index}") for index in range(MAX_DIAGNOSTICS_PER_BATCH)
        ),
    ).source_key == source

    invalid_batches = (
        {"source": source, "work_facts": (_work_fact(source=sibling),)},
        {
            "source": source,
            "request_facts": (_request_fact(work_key=_work_key(source=sibling)),),
        },
        {"source": source, "work_facts": (work, work)},
        {"source": source, "request_facts": (request, request)},
        {
            "source": source,
            "work_facts": tuple(
                _work_fact(f"work:{index}") for index in range(MAX_WORK_FACTS_PER_BATCH + 1)
            ),
        },
        {
            "source": source,
            "request_facts": tuple(
                _request_fact(f"request:{index}")
                for index in range(MAX_REQUEST_FACTS_PER_BATCH + 1)
            ),
        },
        {
            "source": source,
            "diagnostics": tuple(
                _diagnostic(f"partial_{index}")
                for index in range(MAX_DIAGNOSTICS_PER_BATCH + 1)
            ),
        },
    )
    for fields in invalid_batches:
        with pytest.raises(ProviderFactValidationError):
            _batch(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "safe_label",
    [
        "/Users/private/work:01",
        "Fix this prompt?",
        "Codex\nwork:01",
        "Codex person@example.com",
        "Codex Bearer_secret",
        "Claude work:01",
        "Codex other-work",
        "x" * 65,
    ],
)
def test_safe_label_refuses_path_prompt_control_and_secret_shaped_copy(
    safe_label: str,
) -> None:
    """Provider payload copy must not cross into canonical display-safe work facts."""
    work = _work_key()
    with pytest.raises(ProviderFactValidationError, match="invalid safe label"):
        ProviderWorkFact(
            key=work,
            lifecycle=WorkLifecycle.ACTIVE,
            watermark=_watermark(),
            safe_label=safe_label,
            parent_key=None,
            next_actor=NextActor.PROVIDER,
        )


def test_secret_shaped_opaque_work_id_cannot_become_a_matching_safe_label() -> None:
    """Opaque grammar alone must not let a credential marker become canonical UI copy."""
    with pytest.raises(ProviderFactValidationError, match="invalid opaque identifier"):
        secret_key = WorkKey(_source(), WorkIdentifier("work:Bearer_secret"))
        ProviderWorkFact(
            key=secret_key,
            lifecycle=WorkLifecycle.ACTIVE,
            watermark=_watermark(),
            safe_label="Codex work:Bearer_secret",
            parent_key=None,
            next_actor=NextActor.PROVIDER,
        )


def test_fact_types_validate_sources_parents_diagnostics_and_quota_windows() -> None:
    """Annotations alone must not admit cross-source facts or malformed quota values."""
    source = _source()
    sibling = _source(instance="local:02")
    sibling_work = _work_key(source=sibling)
    lane = QuotaLaneKey(
        source,
        "all",
        "requests",
        None,
        "session",
        QuotaEffect.ALL_WORKLOADS,
    )
    window = ProviderQuotaWindow(
        lane_key=lane,
        used_percent=25.0,
        window_minutes=300,
        reset_epoch=1_800_003_600.0,
        watermark=_watermark(source=source),
        source_health=SourceHealth.HEALTHY,
        partial=False,
    )

    assert window.used_percent == 25.0
    assert window.lane_key.source == window.watermark.source_key
    with pytest.raises(ProviderFactValidationError, match="invalid work parent"):
        _work_fact(parent_key=sibling_work)
    with pytest.raises(ProviderFactValidationError, match="invalid quota window"):
        ProviderQuotaWindow(
            lane_key=lane,
            used_percent=101.0,
            window_minutes=300,
            reset_epoch=1_800_003_600.0,
            watermark=_watermark(source=source),
            source_health=SourceHealth.HEALTHY,
            partial=False,
        )
    with pytest.raises(ProviderFactValidationError, match="invalid quota window"):
        ProviderQuotaWindow(
            lane_key=lane,
            used_percent=25.0,
            window_minutes=300,
            reset_epoch=1_800_003_600.0,
            watermark=_watermark(source=sibling),
            source_health=SourceHealth.HEALTHY,
            partial=False,
        )
    with pytest.raises(ProviderFactValidationError, match="invalid diagnostic"):
        ProviderFactDiagnostic(DiagnosticIdentifier("partial"), 0)
    with pytest.raises(CapacityValidationError, match="invalid quota lane key"):
        QuotaLaneKey(
            source,
            "all",
            "requests",
            None,
            "Session window",
            QuotaEffect.ALL_WORKLOADS,
        )


class _PoisonString(str):
    def __str__(self) -> str:
        raise AssertionError("poison string was stringified")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("poison string was iterated")


class _PoisonFloat(float):
    def __float__(self) -> float:
        raise AssertionError("poison float was converted")


class _PoisonTuple(tuple[object, ...]):
    def __iter__(self) -> Iterator[object]:
        raise AssertionError("poison tuple was iterated")


def test_poison_subclasses_are_rejected_without_executing_attacker_behavior() -> None:
    """Subclass hooks must not run while rejecting untrusted provider fact shapes."""
    with pytest.raises(ProviderFactValidationError, match="invalid opaque identifier"):
        WorkIdentifier(_PoisonString("work:01"))
    with pytest.raises(ProviderFactValidationError, match="invalid provider watermark"):
        _watermark(epoch=_PoisonFloat(1_800_000_000.0))
    with pytest.raises(ProviderFactValidationError, match="invalid work facts"):
        _batch(work_facts=_PoisonTuple())  # type: ignore[arg-type]

    valid_payload = work_key_to_payload(_work_key())
    valid_payload["work_id"] = _PoisonString("work:01")
    assert work_key_from_payload(valid_payload) is None


def test_public_fact_enums_preserve_distinct_authority_and_truth_states() -> None:
    """Collapsing authority, freshness, lifecycle, or request states loses fact truth."""
    assert list(ObservationAuthority) == [
        ObservationAuthority.UNTRUSTED_HINT,
        ObservationAuthority.RESTORED_LAST_KNOWN,
        ObservationAuthority.FALLBACK_OBSERVATION,
        ObservationAuthority.DIRECT_PROVIDER_OBSERVATION,
        ObservationAuthority.AUTHORITATIVE_PROVIDER,
    ]
    assert len(set(SourceHealth)) == 8
    assert len(set(SourceFreshness)) == 6
    assert len(set(WorkLifecycle)) == 6
    assert len(set(NextActor)) == 4
    assert len(set(ProviderRequestState)) == 3
    assert len(set(RequestKind)) == 5
