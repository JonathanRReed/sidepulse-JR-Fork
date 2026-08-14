"""The Codex capacity plane, from the rollout file to the dropdown.

Codex is the provider that actually works on this machine and it is on by
default, and until these tests existed its numbers went from a local rollout
file to the menu bar without ever passing the authority layer:
`adapt_legacy_usage_windows` minted lane keys outside the contract
(pool "unspecified", opaque_scope "legacy:N") and `windows[0]` was rendered as
the ceiling that stops work, whatever that row happened to be.

The load-bearing claims:

  * a Codex window this build did not declare is DROPPED, never force-fitted
    -- a "Spark" allowance read as the 5-hour ceiling is worse than no
    reading, and Codex's window set is genuinely unstable (OpenAI removed the
    5-hour window outright for ~2.5 weeks in 2026);
  * `used_percent` is percent USED and is inverted exactly once;
  * the produced lanes and the refresh scope agree, or the coordinator throws
    every reading away as cross-scope;
  * a result that still carries a raw window list is REFUSED, so there is no
    second route onto the card for any provider;
  * a reading the authority layer forgave as old-but-real is shown WITH the
    card's stale marker, not as if it had just been taken;
  * the execution context matches the whole SourceKey, so an adapter or a
    capability this build never registered cannot borrow a registered one.
"""

from __future__ import annotations

import time
from dataclasses import replace as dataclass_replace
from unittest.mock import patch

import pytest

from sidepulse import usage_stats
from sidepulse.capacity_refresh import RefreshCause
from sidepulse.capacity_sources import (
    EvidenceMetricKind,
    SupportedCapacityEvidence,
    SupportedLaneEvidence,
    normalize_supported_quota_evidence,
)
from sidepulse.capacity_types import (
    CapacitySnapshot,
    CapacitySourceHealth,
    CapacityUnit,
    CapacityValue,
    ObservationState,
    QuotaEffect,
    QuotaHorizon,
    QuotaLaneKey,
    QuotaLaneObservation,
    ResetFact,
    ResetState,
    SourceHealthKind,
    SourceKey,
)
from sidepulse.provider_capacity import negotiate_provider_capacity_policies
from sidepulse.providers import negotiated_provider_sources
from tests.test_sidepulse import isolate_controller

NOW = 1_800_000_000.0


def _descriptor():
    return next(
        row.descriptor
        for row in negotiate_provider_capacity_policies(negotiated_provider_sources())
        if row.descriptor is not None
        and row.descriptor.source == usage_stats.CODEX_QUOTA_SOURCE
    )


def _observations(limits, *, observed_at=NOW):
    """Everything the codex producer does, from raw payload to typed lanes."""
    descriptor = _descriptor()
    windows = usage_stats.codex_windows_from_limits(limits)
    evidence = usage_stats.codex_capacity_evidence_from_windows(
        descriptor,
        windows,
        observed_at=observed_at,
    )
    return normalize_supported_quota_evidence(
        descriptor,
        evidence,
        observed_at=observed_at,
    ).snapshot.lanes


@pytest.fixture
def controller(request):
    class ControllerCase:
        def __init__(self) -> None:
            self._cleanups = []

        def addCleanup(self, callback) -> None:
            self._cleanups.append(callback)

        def skipTest(self, reason: str) -> None:
            pytest.skip(reason)

        def close(self) -> None:
            for callback in reversed(self._cleanups):
                callback()

    case = ControllerCase()
    isolate_controller(case)
    request.addfinalizer(case.close)
    return case.controller, case.status_bar


# --------------------------------------------------------------------------
# The mapping boundary.
# --------------------------------------------------------------------------


def test_only_the_two_declared_codex_ceilings_become_lanes() -> None:
    """An undeclared Codex window must be dropped, not borrow a declared lane."""
    lanes = _observations(
        {
            "primary": {"used_percent": 25.0, "window_minutes": 300},
            "secondary": {"used_percent": 70.0, "window_minutes": 7 * 24 * 60},
            "additional_rate_limits": [
                {
                    "name": "GPT-5.3-Codex-Spark",
                    "used_percent": 97.0,
                    "window_minutes": 300,
                }
            ],
        }
    )

    assert tuple(lane.semantic_name for lane in lanes) == ("5-hour", "Weekly")
    assert all(lane.key.pool == "codex-chatgpt-plan" for lane in lanes)
    assert all(lane.key.opaque_scope == "all" for lane in lanes)
    assert tuple(lane.key.window for lane in lanes) == ("five-hour", "weekly")
    assert tuple(lane.horizon for lane in lanes) == (
        QuotaHorizon.SHORT,
        QuotaHorizon.LONG,
    )


def test_a_payload_with_no_declared_window_produces_no_lane_at_all() -> None:
    """`windows[0]` was the ceiling, and it is not always a ceiling.

    Codex may publish only model-specific sub-caps -- and did publish no
    5-hour window at all for about two and a half weeks in 2026. The legacy
    path took whatever sat first in the list and rendered it as the limit that
    stops work.
    """
    lanes = _observations(
        {
            "additional_rate_limits": [
                {
                    "name": "GPT-5.3-Codex-Spark",
                    "used_percent": 97.0,
                    "window_minutes": 300,
                }
            ]
        }
    )

    assert lanes == ()


def test_the_plans_own_window_outranks_a_same_named_extra_allowance() -> None:
    """An extra allowance must not displace the ceiling it shares a name with."""
    lanes = _observations(
        {
            "primary": {"used_percent": 25.0, "window_minutes": 300},
            "additional_rate_limits": [
                {"name": "primary", "used_percent": 99.0, "window_minutes": 300}
            ],
        }
    )

    assert tuple(lane.semantic_name for lane in lanes) == ("5-hour",)
    assert lanes[0].value.remaining == 75.0


def test_used_percent_is_inverted_exactly_once() -> None:
    lanes = _observations({"primary": {"used_percent": 88.0, "window_minutes": 300}})

    assert lanes[0].value.unit is CapacityUnit.PERCENT_REMAINING
    assert lanes[0].value.remaining == 12.0


def test_a_codex_window_with_no_credible_reset_stays_reset_less() -> None:
    """Nothing may render a countdown that was never observed."""
    lanes = _observations(
        {
            "primary": {
                "used_percent": 25.0,
                "window_minutes": 300,
                "resets_at": "not a timestamp",
            }
        }
    )

    assert lanes[0].reset.state is ResetState.UNKNOWN
    assert lanes[0].reset.reset_epoch is None


def test_the_producer_only_speaks_for_its_own_declared_source() -> None:
    from sidepulse import claude_quota

    claude_descriptor = next(
        row.descriptor
        for row in negotiate_provider_capacity_policies(negotiated_provider_sources())
        if row.descriptor is not None
        and row.descriptor.source == claude_quota.CLAUDE_QUOTA_SOURCE
    )

    with pytest.raises(ValueError):
        usage_stats.codex_capacity_evidence_from_windows(
            claude_descriptor,
            [{"label": "primary", "used_percent": 10.0}],
            observed_at=NOW,
        )


# --------------------------------------------------------------------------
# End to end: a real refresh reaches the dropdown, with nothing raw in it.
# --------------------------------------------------------------------------


def _run_codex_refresh(target, status_bar, limits):
    """Drive one real codex capacity refresh through the worker and publish."""
    target.rebuild_capacity_refresh_coordinator()
    refresh_key = target._capacity_refresh_keys_by_provider["codex"]
    source_key = refresh_key.source
    monotonic_now = time.monotonic()
    decision = target._capacity_refresh_coordinator.request_refresh(
        refresh_key,
        RefreshCause.MANUAL,
        monotonic_now,
    )
    generation = decision.generation
    target._capacity_refresh_coordinator.register_started(
        refresh_key,
        generation,
        monotonic_now + 30.0,
    )
    published = []
    with (
        patch.object(
            status_bar.usage_stats,
            "cached_codex_rate_limits",
            return_value=limits,
        ),
        patch.object(
            target,
            "performSelectorOnMainThread_withObject_waitUntilDone_",
            side_effect=lambda _sel, payload, _wait: published.append(payload),
        ),
    ):
        target._usage_refresh_source_worker(source_key, generation, None, {}, None)

    assert published, "the worker published nothing"
    with patch.object(target, "schedule_capacity_timers"):
        target.applyUsageSummary_(published[0])
    return published[0]


def _live_limits():
    reset = time.time() + 3_600.0
    return {
        "primary": {
            "used_percent": 25.0,
            "window_minutes": 300,
            "resets_at": reset,
        },
        "secondary": {
            "used_percent": 70.0,
            "window_minutes": 7 * 24 * 60,
            "resets_at": reset + 86_400.0,
        },
    }


def _drifted_limits():
    """Every label outside `CODEX_LANE_IDENTITIES` -- total schema drift."""
    reset = time.time() + 3_600.0
    return {
        "additional_rate_limits": [
            {
                "name": "GPT-5.3-Codex-Spark",
                "used_percent": 97.0,
                "window_minutes": 300,
                "resets_at": reset,
            },
            {
                "name": "Fable",
                "used_percent": 91.0,
                "window_minutes": 7 * 24 * 60,
                "resets_at": reset + 86_400.0,
            },
        ]
    }


def _rendered_capacity_text(target, status_bar) -> str:
    """Every string the owner can actually read, menu and Settings."""
    status_bar.build_usage_menu_item(target)
    model = target._usage_provider_models["codex"]
    return " | ".join(
        (
            target._usage_menu_labels["codex"].stringValue(),
            target._usage_menu_secondary_labels["codex"].stringValue(),
            *(
                field.stringValue()
                for field in target._usage_menu_window_labels["codex"]
            ),
            model.menu_line,
            model.settings_text,
        )
    )


def test_a_codex_refresh_publishes_contract_stamped_observations(controller) -> None:
    """The worker's result carries typed observations and no raw list at all."""
    target, status_bar = controller

    payload = _run_codex_refresh(target, status_bar, _live_limits())

    source = target._capacity_refresh_keys_by_provider["codex"].source
    result = payload["results"][source]
    # The raw list never even reaches the main thread.
    assert "windows" not in result
    observations = result["capacity_observations"]
    assert type(observations) is tuple
    assert tuple(lane.semantic_name for lane in observations) == ("5-hour", "Weekly")
    assert type(result["source_generation"]) is int

    # The produced lanes and the refresh key agree, or the coordinator throws
    # the whole reading away as cross-scope and the card silently shows
    # nothing. This is the assertion that catches a discriminator invented in
    # one place and not the other.
    state = next(
        row
        for row in target._capacity_refresh_coordinator.snapshot_state(
            time.monotonic()
        ).sources
        if row.key == target._capacity_refresh_keys_by_provider["codex"]
    )
    assert state.has_last_known_good
    assert len(state.last_known_good.lanes) == 2


def test_every_codex_ceiling_reaches_the_dropdown_named_and_numbered(
    controller,
) -> None:
    target, status_bar = controller

    _run_codex_refresh(target, status_bar, _live_limits())

    model = target._usage_provider_models["codex"]
    assert len(model.windows) == 2
    # Contract identity, not `legacy:N` keys minted by the display adapter.
    assert {window.lane_key.pool for window in model.windows} == {"codex-chatgpt-plan"}
    assert {window.lane_key.opaque_scope for window in model.windows} == {"all"}

    status_bar.build_usage_menu_item(target)

    assert target._usage_menu_labels["codex"].stringValue() == "Codex · 5h 75% left"
    assert "resets in" in target._usage_menu_secondary_labels["codex"].stringValue()
    assert tuple(
        field.stringValue() for field in target._usage_menu_window_labels["codex"]
    ) == ("Weekly 30% left · resets in 1d 1h",)
    assert model.settings_text == "5h 75% left · 7d 30% left"


def test_a_drifted_codex_payload_renders_nothing_rather_than_raw_percentages(
    controller,
) -> None:
    """The provider in daily use must not publish an unauthorised percentage.

    Before the producer existed, `applyUsageSummary_` took
    `result["windows"]` straight to `adapt_legacy_usage_windows` for codex,
    which minted `pool="unspecified"`, `opaque_scope="legacy:N"` keys and a
    countdown off an unvalidated `resets_at`. A "Spark" allowance then
    rendered as `Codex · 5h 3% left`, indistinguishable from the ceiling that
    actually stops work.
    """
    target, status_bar = controller

    payload = _run_codex_refresh(target, status_bar, _drifted_limits())

    source = target._capacity_refresh_keys_by_provider["codex"].source
    result = payload["results"][source]
    assert "windows" not in result
    assert result["capacity_observations"] == ()

    model = target._usage_provider_models["codex"]
    assert model.windows == ()
    assert model.error_text == status_bar.CAPACITY_UNAUTHORISED_COPY

    rendered = _rendered_capacity_text(target, status_bar)
    assert target._usage_menu_labels["codex"].stringValue() == (
        "Codex · Capacity reading unavailable"
    )
    assert target._usage_menu_window_labels["codex"] == ()
    assert model.settings_text == status_bar.CAPACITY_UNAUTHORISED_COPY
    # No number, no label the contract never declared, no invented countdown.
    assert "%" not in rendered
    assert "Spark" not in rendered
    assert "Fable" not in rendered
    assert "resets in" not in rendered.lower()


def test_one_refused_lane_is_counted_beside_the_one_that_survived(
    controller,
) -> None:
    """A row that vanishes silently is worse than a row that says why."""
    target, status_bar = controller
    descriptor = _descriptor()
    observed_at = time.time()
    evidence = SupportedCapacityEvidence(
        source=usage_stats.CODEX_QUOTA_SOURCE,
        health_kind=SourceHealthKind.HEALTHY,
        lanes=(
            SupportedLaneEvidence(
                key=descriptor.lanes[0].key,
                metric_kind=EvidenceMetricKind.PERCENT_USED,
                percent=25.0,
                state=ObservationState.OBSERVED,
                reset_state=ResetState.UNKNOWN,
                reset_epoch=None,
                window_minutes=300.0,
            ),
            SupportedLaneEvidence(
                key=descriptor.lanes[1].key,
                metric_kind=EvidenceMetricKind.PERCENT_USED,
                percent=70.0,
                state=ObservationState.PARTIAL,
                reset_state=ResetState.UNKNOWN,
                reset_epoch=None,
                window_minutes=7 * 24 * 60.0,
            ),
        ),
        account_discriminator=usage_stats.CODEX_ACCOUNT_SCOPE,
        has_last_known_good=False,
        auth_mode=None,
    )
    lanes = normalize_supported_quota_evidence(
        descriptor,
        evidence,
        observed_at=observed_at,
    ).snapshot.lanes

    target.rebuild_capacity_refresh_coordinator()
    refresh_key = target._capacity_refresh_keys_by_provider["codex"]
    monotonic_now = time.monotonic()
    decision = target._capacity_refresh_coordinator.request_refresh(
        refresh_key,
        RefreshCause.MANUAL,
        monotonic_now,
    )
    target._capacity_refresh_coordinator.register_started(
        refresh_key,
        decision.generation,
        monotonic_now + 30.0,
    )
    with patch.object(target, "schedule_capacity_timers"):
        target.applyUsageSummary_(
            {
                "requests": {refresh_key.source: decision.generation},
                "results": {
                    refresh_key.source: {
                        "provider_id": "codex",
                        "title": "Codex",
                        "capacity_observations": lanes,
                        "capacity_requested": True,
                        "source_generation": 1,
                    }
                },
                "failures": {},
            }
        )

    model = target._usage_provider_models["codex"]
    assert tuple(window.label for window in model.windows) == ("5-hour",)
    assert model.error_text == "1 window unavailable"
    assert "70" not in model.settings_text


def test_turning_codex_percent_off_is_not_reported_as_a_fault(controller) -> None:
    """An opt-out and an unusable source both authorise nothing, and differ.

    "Capacity reading unavailable" over a switch the owner deliberately threw
    is the one message a status bar must never cry wolf with.
    """
    target, status_bar = controller
    target.settings = dataclass_replace(target.settings, codex_percent_enabled=False)

    payload = _run_codex_refresh(target, status_bar, _live_limits())

    source = target._capacity_refresh_keys_by_provider["codex"].source
    result = payload["results"][source]
    assert result["capacity_requested"] is False
    assert result["capacity_observations"] == ()

    model = target._usage_provider_models["codex"]
    assert model.windows == ()
    assert model.error_text is None
    assert status_bar.CAPACITY_UNAUTHORISED_COPY not in model.settings_text


def test_the_publish_path_refuses_a_result_that_still_carries_raw_windows(
    controller,
) -> None:
    """The contract is the only route in, for every provider.

    A result carrying a raw window list is refused outright rather than
    rendered, so no producer can reintroduce the bypass by shipping both.
    """
    target, _status_bar = controller
    target.rebuild_capacity_refresh_coordinator()
    refresh_key = target._capacity_refresh_keys_by_provider["codex"]
    monotonic_now = time.monotonic()
    decision = target._capacity_refresh_coordinator.request_refresh(
        refresh_key,
        RefreshCause.MANUAL,
        monotonic_now,
    )
    assert decision.generation is not None
    target._capacity_refresh_coordinator.register_started(
        refresh_key,
        decision.generation,
        monotonic_now + 30.0,
    )

    with patch.object(target, "schedule_capacity_timers"):
        target.applyUsageSummary_(
            {
                "requests": {refresh_key.source: decision.generation},
                "results": {
                    refresh_key.source: {
                        "provider_id": "codex",
                        "title": "Codex",
                        "capacity_observations": _observations(
                            {"primary": {"used_percent": 25.0, "window_minutes": 300}},
                            observed_at=time.time(),
                        ),
                        "windows": [
                            {
                                "label": "Spark",
                                "used_percent": 97.0,
                                "window_minutes": 300,
                            }
                        ],
                    }
                },
                "failures": {},
            }
        )

    assert target._usage_provider_states["codex"].consecutive_failures == 1
    model = target._usage_provider_models["codex"]
    assert model.windows == ()
    assert "3%" not in model.settings_text


def test_a_capacity_result_with_no_observations_key_never_publishes(
    controller,
) -> None:
    """A producer that never ran is not a producer that authorised nothing."""
    target, _status_bar = controller
    target.rebuild_capacity_refresh_coordinator()
    refresh_key = target._capacity_refresh_keys_by_provider["codex"]
    monotonic_now = time.monotonic()
    decision = target._capacity_refresh_coordinator.request_refresh(
        refresh_key,
        RefreshCause.MANUAL,
        monotonic_now,
    )
    target._capacity_refresh_coordinator.register_started(
        refresh_key,
        decision.generation,
        monotonic_now + 30.0,
    )

    with patch.object(target, "schedule_capacity_timers"):
        target.applyUsageSummary_(
            {
                "requests": {refresh_key.source: decision.generation},
                "results": {
                    refresh_key.source: {"provider_id": "codex", "title": "Codex"}
                },
                "failures": {},
            }
        )

    assert target._usage_provider_states["codex"].consecutive_failures == 1


# --------------------------------------------------------------------------
# Forgiven readings have to LOOK forgiven.
# --------------------------------------------------------------------------


def _forgiven_observations(*, health_kind, has_last_known_good, observed_at):
    """Contract-stamped lanes from a source the authority layer forgives."""
    descriptor = _descriptor()
    evidence = SupportedCapacityEvidence(
        source=usage_stats.CODEX_QUOTA_SOURCE,
        health_kind=health_kind,
        lanes=tuple(
            SupportedLaneEvidence(
                key=lane.key,
                metric_kind=EvidenceMetricKind.PERCENT_USED,
                percent=40.0,
                state=ObservationState.OBSERVED,
                reset_state=ResetState.FUTURE,
                reset_epoch=observed_at + 3_600.0,
                window_minutes=300.0,
            )
            for lane in descriptor.lanes[:1]
        ),
        account_discriminator=usage_stats.CODEX_ACCOUNT_SCOPE,
        has_last_known_good=has_last_known_good,
        auth_mode=None,
    )
    return normalize_supported_quota_evidence(
        descriptor,
        evidence,
        observed_at=observed_at,
    ).snapshot.lanes


def test_the_display_gate_carries_the_freshness_it_decided(controller) -> None:
    """`LaneAuthority.freshness` was computed and then dropped on the floor."""
    _target, status_bar = controller
    lanes = _forgiven_observations(
        health_kind=SourceHealthKind.ACCESS_DENIED,
        has_last_known_good=True,
        observed_at=NOW,
    )
    snapshot = CapacitySnapshot(NOW, lanes, (lanes[0].source_health,))

    authorised = status_bar.authorised_capacity_lanes(snapshot, now=NOW)

    # Forgiven, so still shown -- that part is deliberate.
    assert tuple(lane.semantic_name for lane in authorised.lanes) == ("5-hour",)
    assert authorised.freshness == (("5-hour", ObservationState.LAST_KNOWN_GOOD),)
    assert authorised.stale is True


def test_a_fresh_reading_is_not_marked_stale(controller) -> None:
    """The marker has to mean something, so it must not be on by default."""
    _target, status_bar = controller
    lanes = _forgiven_observations(
        health_kind=SourceHealthKind.HEALTHY,
        has_last_known_good=False,
        observed_at=NOW,
    )
    snapshot = CapacitySnapshot(NOW, lanes, (lanes[0].source_health,))

    authorised = status_bar.authorised_capacity_lanes(snapshot, now=NOW)

    assert authorised.freshness == (("5-hour", ObservationState.OBSERVED),)
    assert authorised.stale is False


@pytest.mark.parametrize(
    ("health_kind", "has_last_known_good"),
    (
        (SourceHealthKind.ACCESS_DENIED, True),
        (SourceHealthKind.FAILED, True),
        (SourceHealthKind.STALE, False),
    ),
)
def test_a_forgiven_reading_renders_with_the_cards_stale_marker(
    controller,
    health_kind,
    has_last_known_good,
) -> None:
    """A number the layer called old rendered identically to a fresh one.

    `_value_refusal` forgives an unreachable source that still holds a
    last-known-good reading, and for DISPLAY that is right -- the card keeps
    showing the number. It just never said so: the refresh itself succeeded,
    so `last_success_at` was fresh, `error_text` was None, and every
    clock-derived staleness test called the card current. A source that had
    answered ACCESS_DENIED published a percentage, a live countdown and
    "updated just now" with no marker anywhere.
    """
    target, status_bar = controller
    target.rebuild_capacity_refresh_coordinator()
    refresh_key = target._capacity_refresh_keys_by_provider["codex"]
    monotonic_now = time.monotonic()
    decision = target._capacity_refresh_coordinator.request_refresh(
        refresh_key,
        RefreshCause.MANUAL,
        monotonic_now,
    )
    target._capacity_refresh_coordinator.register_started(
        refresh_key,
        decision.generation,
        monotonic_now + 30.0,
    )
    observed_at = time.time()

    with patch.object(target, "schedule_capacity_timers"):
        target.applyUsageSummary_(
            {
                "requests": {refresh_key.source: decision.generation},
                "results": {
                    refresh_key.source: {
                        "provider_id": "codex",
                        "title": "Codex",
                        "capacity_observations": _forgiven_observations(
                            health_kind=health_kind,
                            has_last_known_good=has_last_known_good,
                            observed_at=observed_at,
                        ),
                        "capacity_requested": True,
                        "source_generation": 1,
                    }
                },
                "failures": {},
            }
        )

    model = target._usage_provider_models["codex"]
    # The number survives -- forgiveness is the point.
    assert model.windows[0].percent_remaining == 60.0
    # And it is visibly old, in the card's own existing vocabulary.
    assert model.stale is True
    assert "stale" in model.menu_line
    assert "Stale" in model.settings_text
    # A countdown taken from a reading the source can no longer stand behind
    # is exactly the thing the stale reset state exists to suppress.
    assert model.windows[0].reset_state is ResetState.STALE
    assert model.windows[0].reset_known is False

    status_bar.build_usage_menu_item(target)
    secondary = target._usage_menu_secondary_labels["codex"].stringValue()
    assert "stale" in secondary
    assert "resets" not in secondary


def test_a_healthy_reading_publishes_without_the_stale_marker(controller) -> None:
    """Guards the other direction: the marker must not be permanently on."""
    target, status_bar = controller

    _run_codex_refresh(target, status_bar, _live_limits())

    model = target._usage_provider_models["codex"]
    assert model.stale is False
    assert "stale" not in model.menu_line
    assert "Stale" not in model.settings_text


# --------------------------------------------------------------------------
# The execution context matches the WHOLE source identity.
# --------------------------------------------------------------------------


def _foreign_lane(source: SourceKey) -> QuotaLaneObservation:
    return QuotaLaneObservation(
        key=QuotaLaneKey(
            source,
            "all",
            "codex-chatgpt-plan",
            None,
            "five-hour",
            QuotaEffect.ALL_WORKLOADS,
        ),
        semantic_name="Borrowed window",
        horizon=QuotaHorizon.SHORT,
        value=CapacityValue(CapacityUnit.PERCENT_REMAINING, 60.0, ObservationState.OBSERVED),
        reset=ResetFact(ResetState.FUTURE, NOW + 3_600.0, 300.0, NOW),
        observed_at=NOW,
        source_health=CapacitySourceHealth(
            source,
            SourceHealthKind.HEALTHY,
            NOW,
            NOW,
            None,
            None,
            False,
        ),
        account_discriminator=None,
    )


def test_the_production_context_names_whole_source_identities(controller) -> None:
    _target, status_bar = controller
    context = status_bar.capacity_execution_context()

    assert context.source_keys == (
        SourceKey("claude", "quota", "oauth", "remote_quota_windows"),
        SourceKey("codex", "quota", "local", "remote_quota_windows"),
    )
    # The pairs remain as a projection of the identities, never an
    # independent claim.
    assert context.source_scopes == (("claude", "oauth"), ("codex", "local"))


@pytest.mark.parametrize(
    ("provider_id", "adapter_id", "instance", "capability_id"),
    (
        # Right provider, right instance, an adapter never registered for
        # capacity. Two of four fields matched, so this was APPLICABLE.
        ("claude", "transcripts", "oauth", "remote_quota_windows"),
        ("codex", "transcripts", "local", "remote_quota_windows"),
        # Right provider, adapter and instance, read through a capability that
        # grants no quota authority at all.
        ("codex", "quota", "local", "transcript_usage"),
        ("claude", "quota", "oauth", "transcript_usage"),
        # And the pairs that were already refused stay refused.
        ("codex", "quota", "oauth", "remote_quota_windows"),
        ("claude", "quota", "local", "remote_quota_windows"),
    ),
)
def test_a_source_differing_in_any_component_is_out_of_context(
    controller,
    provider_id,
    adapter_id,
    instance,
    capability_id,
) -> None:
    """A SourceKey has four components and two of them were compared."""
    _target, status_bar = controller
    foreign = _foreign_lane(
        SourceKey(provider_id, adapter_id, instance, capability_id)
    )
    snapshot = CapacitySnapshot(NOW, (foreign,), (foreign.source_health,))

    authorised = status_bar.authorised_capacity_lanes(snapshot, now=NOW)

    assert authorised.lanes == ()
    assert authorised.withheld == (("Borrowed window", "source_out_of_context"),)


def test_a_registered_source_is_still_authorised(controller) -> None:
    """The exact-identity match must not refuse the real sources too."""
    _target, status_bar = controller
    lanes = _observations(
        {"primary": {"used_percent": 25.0, "window_minutes": 300}},
        observed_at=NOW,
    )
    snapshot = CapacitySnapshot(NOW, lanes, (lanes[0].source_health,))

    authorised = status_bar.authorised_capacity_lanes(snapshot, now=NOW)

    assert tuple(lane.semantic_name for lane in authorised.lanes) == ("5-hour",)
    assert authorised.withheld == ()


def test_a_context_cannot_name_pairs_that_contradict_its_identities() -> None:
    """The pairs are derived from the identities, never asserted beside them."""
    from sidepulse.capacity_types import CapacityValidationError, ExecutionContext

    with pytest.raises(CapacityValidationError):
        ExecutionContext(
            provider_ids=("claude", "codex"),
            source_instances=("local", "oauth"),
            selected_model=None,
            selected_feature=None,
            source_scopes=(("claude", "local"), ("codex", "oauth")),
            source_keys=(
                SourceKey("claude", "quota", "oauth", "remote_quota_windows"),
                SourceKey("codex", "quota", "local", "remote_quota_windows"),
            ),
        )
